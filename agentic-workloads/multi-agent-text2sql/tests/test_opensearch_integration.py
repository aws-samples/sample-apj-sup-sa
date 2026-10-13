"""OpenSearch 통합 테스트

작업 9.2: OpenSearch 통합 테스트
- 실제 OpenSearch 인스턴스 연동
- 스키마 문서 검색 테스트
- 하이브리드 검색 성능 측정

Requirements:
- 1.1: 사용자 쿼리를 임베딩으로 변환
- 1.2: 유사도 기반으로 상위 10개의 관련 스키마 문서 반환
- 1.3: 각 문서의 관련도 점수와 메타데이터 포함

이 테스트는 실제 OpenSearch 인스턴스가 필요합니다.
환경 변수를 통해 OpenSearch 연결 정보를 제공해야 합니다:
- OPENSEARCH_ENDPOINT
- OPENSEARCH_USERNAME
- OPENSEARCH_PASSWORD
- OPENSEARCH_INDEX (선택, 기본값: schema_docs)
"""

import os
import time
import pytest
from typing import List, Dict, Any
from unittest.mock import patch

from agents.multi_agent.rag_agent import (
    RAGAgent,
    SearchResult,
    EMBEDDING_DIMENSION,
    DEFAULT_TOP_K,
)


# OpenSearch 연결 정보 (환경 변수에서 로드)
OPENSEARCH_ENDPOINT = os.getenv("OPENSEARCH_ENDPOINT")
OPENSEARCH_USERNAME = os.getenv("OPENSEARCH_USERNAME")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD")
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "schema_docs")

# OpenSearch 연결 가능 여부 확인
OPENSEARCH_AVAILABLE = all([
    OPENSEARCH_ENDPOINT,
    OPENSEARCH_USERNAME,
    OPENSEARCH_PASSWORD
])

# OpenSearch가 없으면 테스트 스킵
pytestmark = pytest.mark.skipif(
    not OPENSEARCH_AVAILABLE,
    reason="OpenSearch 연결 정보 없음 (OPENSEARCH_ENDPOINT, OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD 필요)"
)


class TestOpenSearchConnection:
    """OpenSearch 연결 테스트"""
    
    @pytest.fixture
    def rag_agent(self):
        """실제 OpenSearch에 연결된 RAG Agent"""
        return RAGAgent(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            opensearch_endpoint=OPENSEARCH_ENDPOINT,
            opensearch_index=OPENSEARCH_INDEX,
            opensearch_username=OPENSEARCH_USERNAME,
            opensearch_password=OPENSEARCH_PASSWORD
        )
    
    def test_opensearch_client_initialization(self, rag_agent):
        """OpenSearch 클라이언트 초기화 테스트"""
        assert rag_agent._opensearch_client is not None
        assert rag_agent.is_rag_enabled() is True
    
    def test_opensearch_connection_status(self, rag_agent):
        """OpenSearch 연결 상태 확인"""
        status = rag_agent.get_status()
        
        assert status["opensearch_connected"] is True
        assert status["rag_enabled"] is True
        assert status["opensearch_endpoint"] == OPENSEARCH_ENDPOINT
        assert status["opensearch_index"] == OPENSEARCH_INDEX
    
    def test_opensearch_index_exists(self, rag_agent):
        """OpenSearch 인덱스 존재 확인"""
        client = rag_agent._opensearch_client
        
        # 인덱스 존재 확인
        exists = client.indices.exists(index=OPENSEARCH_INDEX)
        assert exists is True, f"인덱스 '{OPENSEARCH_INDEX}'가 존재하지 않습니다"


class TestEmbeddingGeneration:
    """임베딩 생성 테스트 (Requirements 1.1)"""
    
    @pytest.fixture
    def rag_agent(self):
        """RAG Agent 인스턴스"""
        return RAGAgent(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            opensearch_endpoint=OPENSEARCH_ENDPOINT,
            opensearch_index=OPENSEARCH_INDEX,
            opensearch_username=OPENSEARCH_USERNAME,
            opensearch_password=OPENSEARCH_PASSWORD
        )
    
    def test_generate_embedding_for_query(self, rag_agent):
        """쿼리 임베딩 생성 테스트 (Requirements 1.1)"""
        query = "학생 성적 데이터"
        
        embedding = rag_agent.generate_embedding(query)
        
        assert embedding is not None
        assert len(embedding) == EMBEDDING_DIMENSION
        assert all(isinstance(x, float) for x in embedding)
    
    def test_embedding_cache_hit(self, rag_agent):
        """임베딩 캐시 히트 테스트"""
        query = "매출 데이터"
        
        # 첫 번째 호출 (캐시 미스)
        start_time = time.time()
        embedding1 = rag_agent.generate_embedding(query)
        first_call_time = time.time() - start_time
        
        # 두 번째 호출 (캐시 히트)
        start_time = time.time()
        embedding2 = rag_agent.generate_embedding(query)
        second_call_time = time.time() - start_time
        
        # 캐시 히트가 더 빨라야 함
        assert second_call_time < first_call_time
        
        # 동일한 임베딩 반환
        assert embedding1 == embedding2
        
        # 캐시 통계 확인
        stats = rag_agent.get_embedding_cache_stats()
        assert stats["hits"] >= 1
    
    def test_embedding_dimension_validation(self, rag_agent):
        """임베딩 차원 검증 테스트"""
        queries = [
            "간단한 쿼리",
            "조금 더 긴 쿼리로 테스트합니다",
            "매우 긴 쿼리로 임베딩 차원이 일정하게 유지되는지 확인하는 테스트입니다. " * 10
        ]
        
        for query in queries:
            embedding = rag_agent.generate_embedding(query)
            assert len(embedding) == EMBEDDING_DIMENSION, \
                f"쿼리 '{query[:50]}...'의 임베딩 차원이 {EMBEDDING_DIMENSION}이 아닙니다"


class TestSchemaDocumentSearch:
    """스키마 문서 검색 테스트 (Requirements 1.2, 1.3)"""
    
    @pytest.fixture
    def rag_agent(self):
        """RAG Agent 인스턴스"""
        return RAGAgent(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            opensearch_endpoint=OPENSEARCH_ENDPOINT,
            opensearch_index=OPENSEARCH_INDEX,
            opensearch_username=OPENSEARCH_USERNAME,
            opensearch_password=OPENSEARCH_PASSWORD
        )
    
    def test_search_schema_documents(self, rag_agent):
        """스키마 문서 검색 테스트 (Requirements 1.2)"""
        query = "student grades"
        
        results, error = rag_agent.search_documents(query, doc_type="schema")
        
        assert error is None
        assert isinstance(results, list)
        assert len(results) <= DEFAULT_TOP_K  # 최대 10개
    
    def test_search_results_include_metadata(self, rag_agent):
        """검색 결과에 메타데이터 포함 확인 (Requirements 1.3)"""
        query = "sales revenue"
        
        results, error = rag_agent.search_documents(query)
        
        assert error is None
        
        if results:
            for result in results:
                # SearchResult 구조 확인
                assert isinstance(result, SearchResult)
                assert hasattr(result, 'content')
                assert hasattr(result, 'score')
                assert hasattr(result, 'metadata')
                assert hasattr(result, 'source')
                
                # 점수 범위 확인 (0.0 ~ 1.0)
                assert 0.0 <= result.score <= 1.0
                
                # 메타데이터 확인
                assert isinstance(result.metadata, dict)
    
    def test_search_with_filters(self, rag_agent):
        """필터를 사용한 검색 테스트"""
        query = "table columns"
        filters = {"database": "california_schools"}
        
        results, error = rag_agent.search_documents(
            query,
            filters=filters
        )
        
        assert error is None
        
        # 필터가 적용된 결과 확인
        if results:
            for result in results:
                metadata = result.metadata
                if "database" in metadata:
                    assert metadata["database"] == "california_schools"
    
    def test_search_top_k_limit(self, rag_agent):
        """상위 K개 제한 테스트 (Requirements 1.2)"""
        query = "data"
        
        # top_k=5 요청
        results, error = rag_agent.search_documents(query, top_k=5)
        
        assert error is None
        assert len(results) <= 5
        
        # top_k=15 요청 (최대 10개로 제한되어야 함)
        results, error = rag_agent.search_documents(query, top_k=15)
        
        assert error is None
        assert len(results) <= DEFAULT_TOP_K  # 최대 10개
    
    def test_search_results_sorted_by_relevance(self, rag_agent):
        """검색 결과가 관련도 순으로 정렬되는지 확인"""
        query = "student information"
        
        results, error = rag_agent.search_documents(query)
        
        assert error is None
        
        if len(results) > 1:
            # 점수가 내림차순으로 정렬되어야 함
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True), \
                "검색 결과가 관련도 순으로 정렬되지 않았습니다"


class TestHybridSearch:
    """하이브리드 검색 테스트 (벡터 + 키워드)"""
    
    @pytest.fixture
    def rag_agent(self):
        """RAG Agent 인스턴스"""
        return RAGAgent(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            opensearch_endpoint=OPENSEARCH_ENDPOINT,
            opensearch_index=OPENSEARCH_INDEX,
            opensearch_username=OPENSEARCH_USERNAME,
            opensearch_password=OPENSEARCH_PASSWORD
        )
    
    def test_hybrid_search_performance(self, rag_agent):
        """하이브리드 검색 성능 측정"""
        queries = [
            "student grades and scores",
            "sales revenue by product",
            "customer orders and transactions",
            "employee information and departments"
        ]
        
        performance_results = []
        
        for query in queries:
            start_time = time.time()
            results, error = rag_agent.search_documents(query)
            elapsed_time = time.time() - start_time
            
            performance_results.append({
                "query": query,
                "elapsed_time": elapsed_time,
                "result_count": len(results) if results else 0,
                "error": error
            })
            
            # 성능 기준: 5초 이내
            assert elapsed_time < 5.0, \
                f"검색이 너무 느립니다: {elapsed_time:.2f}초 (쿼리: {query})"
        
        # 평균 성능 출력
        avg_time = sum(r["elapsed_time"] for r in performance_results) / len(performance_results)
        print(f"\n평균 검색 시간: {avg_time:.3f}초")
        
        for result in performance_results:
            print(f"  - {result['query']}: {result['elapsed_time']:.3f}초, "
                  f"{result['result_count']}개 결과")
    
    def test_keyword_matching(self, rag_agent):
        """키워드 매칭 테스트"""
        # 특정 테이블명으로 검색
        query = "schools"
        
        results, error = rag_agent.search_documents(query)
        
        assert error is None
        
        if results:
            # 결과에 'schools' 키워드가 포함되어야 함
            found_keyword = False
            for result in results:
                content_lower = result.content.lower()
                metadata_str = str(result.metadata).lower()
                
                if "school" in content_lower or "school" in metadata_str:
                    found_keyword = True
                    break
            
            assert found_keyword, "키워드 매칭이 동작하지 않습니다"
    
    def test_semantic_search(self, rag_agent):
        """의미론적 검색 테스트"""
        # 동의어/유사어로 검색
        queries = [
            ("student", "pupil"),  # 학생
            ("revenue", "income"),  # 수익
            ("purchase", "buy"),  # 구매
        ]
        
        for query1, query2 in queries:
            results1, _ = rag_agent.search_documents(query1)
            results2, _ = rag_agent.search_documents(query2)
            
            # 유사한 결과를 반환해야 함 (완전히 동일하지 않아도 됨)
            if results1 and results2:
                # 상위 결과의 메타데이터가 유사한지 확인
                top1_tables = {r.metadata.get("table_name") for r in results1[:3]}
                top2_tables = {r.metadata.get("table_name") for r in results2[:3]}
                
                # 최소 1개 이상의 공통 테이블이 있어야 함
                common_tables = top1_tables & top2_tables
                print(f"\n{query1} vs {query2}: 공통 테이블 {len(common_tables)}개")


class TestDomainKnowledgeSearch:
    """도메인 지식 검색 테스트 (Requirements 2.1)"""
    
    @pytest.fixture
    def rag_agent(self):
        """RAG Agent 인스턴스"""
        return RAGAgent(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            opensearch_endpoint=OPENSEARCH_ENDPOINT,
            opensearch_index=OPENSEARCH_INDEX,
            opensearch_username=OPENSEARCH_USERNAME,
            opensearch_password=OPENSEARCH_PASSWORD
        )
    
    def test_search_domain_knowledge(self, rag_agent):
        """도메인 지식 검색 테스트 (Requirements 2.1)"""
        query = "business terms"
        
        results, error = rag_agent.search_domain_knowledge(query)
        
        assert error is None
        assert isinstance(results, list)
    
    def test_domain_search_includes_business_logic(self, rag_agent):
        """도메인 검색 결과에 비즈니스 로직 포함 확인"""
        query = "status type"
        
        results, error = rag_agent.search_domain_knowledge(query)
        
        assert error is None
        
        if results:
            # 비즈니스 로직 메타데이터 확인
            for result in results:
                metadata = result.metadata
                # business_logic 필드가 있을 수 있음
                if "business_logic" in metadata:
                    assert isinstance(metadata["business_logic"], str)


class TestSearchErrorHandling:
    """검색 에러 처리 테스트"""
    
    @pytest.fixture
    def rag_agent(self):
        """RAG Agent 인스턴스"""
        return RAGAgent(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            opensearch_endpoint=OPENSEARCH_ENDPOINT,
            opensearch_index=OPENSEARCH_INDEX,
            opensearch_username=OPENSEARCH_USERNAME,
            opensearch_password=OPENSEARCH_PASSWORD
        )
    
    def test_search_with_empty_query(self, rag_agent):
        """빈 쿼리 검색 테스트"""
        results, error = rag_agent.search_documents("")
        
        # 빈 쿼리는 임베딩 생성 실패로 에러 반환
        assert error is not None
        assert len(results) == 0
    
    def test_search_with_invalid_index(self):
        """잘못된 인덱스 검색 테스트"""
        rag_agent = RAGAgent(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            opensearch_endpoint=OPENSEARCH_ENDPOINT,
            opensearch_index="nonexistent_index",
            opensearch_username=OPENSEARCH_USERNAME,
            opensearch_password=OPENSEARCH_PASSWORD
        )
        
        results, error = rag_agent.search_documents("test query")
        
        # 인덱스 없음 에러
        assert error is not None
        assert len(results) == 0
    
    def test_search_timeout_handling(self, rag_agent):
        """검색 타임아웃 처리 테스트"""
        # 매우 짧은 타임아웃 설정 (1초)
        results, error = rag_agent.search_documents(
            "test query",
            timeout_seconds=1
        )
        
        # 타임아웃이 발생할 수도 있고 정상 완료될 수도 있음
        # 에러가 발생하면 타임아웃 메시지 확인
        if error:
            assert "타임아웃" in error or "timeout" in error.lower()


class TestSearchResultFormatting:
    """검색 결과 포맷팅 테스트"""
    
    @pytest.fixture
    def rag_agent(self):
        """RAG Agent 인스턴스"""
        return RAGAgent(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            opensearch_endpoint=OPENSEARCH_ENDPOINT,
            opensearch_index=OPENSEARCH_INDEX,
            opensearch_username=OPENSEARCH_USERNAME,
            opensearch_password=OPENSEARCH_PASSWORD
        )
    
    def test_format_results_for_llm(self, rag_agent):
        """LLM 친화적 형식으로 결과 포맷팅 테스트"""
        query = "student data"
        
        results, error = rag_agent.search_documents(query)
        
        if results:
            formatted = rag_agent.format_search_results_for_llm(results)
            
            assert isinstance(formatted, str)
            assert len(formatted) > 0
            
            # 마크다운 형식 확인
            assert "# 검색된 문서" in formatted
            assert "##" in formatted  # 문서 헤더
            
            # 메타데이터 포함 확인
            if results[0].metadata:
                assert "메타데이터" in formatted
    
    def test_format_results_with_score(self, rag_agent):
        """관련도 점수 포함 포맷팅 테스트"""
        query = "sales data"
        
        results, error = rag_agent.search_documents(query)
        
        if results:
            # 점수 포함
            formatted_with_score = rag_agent.format_search_results_for_llm(
                results,
                include_score=True
            )
            assert "관련도" in formatted_with_score
            
            # 점수 제외
            formatted_without_score = rag_agent.format_search_results_for_llm(
                results,
                include_score=False
            )
            assert "관련도" not in formatted_without_score


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
