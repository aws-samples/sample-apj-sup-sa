"""RAG Agent 테스트

임베딩 생성, 캐싱, 재시도 로직 등을 테스트합니다.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from agents.multi_agent.rag_agent import (
    EMBEDDING_CACHE_MAX_SIZE,
    EMBEDDING_CACHE_TTL_SECONDS,
    EMBEDDING_DIMENSION,
    MAX_RETRY_ATTEMPTS,
    EmbeddingCacheEntry,
    LRUEmbeddingCache,
    RAGAgent,
)


# =============================================================================
# LRUEmbeddingCache 테스트
# =============================================================================

class TestLRUEmbeddingCache:
    """LRU 임베딩 캐시 테스트"""
    
    def test_cache_put_and_get(self):
        """캐시 저장 및 조회 테스트"""
        cache = LRUEmbeddingCache(max_size=10)
        embedding = [0.1, 0.2, 0.3]
        
        cache.put("test text", embedding)
        result = cache.get("test text")
        
        assert result == embedding
    
    def test_cache_miss_returns_none(self):
        """캐시 미스 시 None 반환 테스트"""
        cache = LRUEmbeddingCache(max_size=10)
        
        result = cache.get("nonexistent")
        
        assert result is None
    
    def test_cache_lru_eviction(self):
        """LRU 제거 정책 테스트"""
        cache = LRUEmbeddingCache(max_size=3)
        
        # 3개 항목 추가
        cache.put("text1", [0.1])
        cache.put("text2", [0.2])
        cache.put("text3", [0.3])
        
        # text1 사용 (LRU 순서 변경)
        cache.get("text1")
        
        # 새 항목 추가 - text2가 제거되어야 함
        cache.put("text4", [0.4])
        
        assert cache.get("text1") is not None
        assert cache.get("text2") is None  # 제거됨
        assert cache.get("text3") is not None
        assert cache.get("text4") is not None
    
    def test_cache_max_size_limit(self):
        """최대 크기 제한 테스트"""
        max_size = 5
        cache = LRUEmbeddingCache(max_size=max_size)
        
        # 최대 크기보다 많은 항목 추가
        for i in range(10):
            cache.put(f"text{i}", [float(i)])
        
        assert len(cache) == max_size
    
    def test_cache_ttl_expiration(self):
        """TTL 만료 테스트"""
        cache = LRUEmbeddingCache(max_size=10, ttl_seconds=1)
        
        cache.put("test", [0.1])
        
        # TTL 전에는 조회 가능
        assert cache.get("test") is not None
        
        # TTL 후에는 만료
        time.sleep(1.1)
        assert cache.get("test") is None
    
    def test_cache_stats(self):
        """캐시 통계 테스트"""
        cache = LRUEmbeddingCache(max_size=10)
        
        cache.put("text1", [0.1])
        cache.get("text1")  # hit
        cache.get("text2")  # miss
        
        stats = cache.get_stats()
        
        assert stats["size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5
    
    def test_cache_clear(self):
        """캐시 초기화 테스트"""
        cache = LRUEmbeddingCache(max_size=10)
        
        cache.put("text1", [0.1])
        cache.put("text2", [0.2])
        cache.clear()
        
        assert len(cache) == 0
        assert cache.get("text1") is None
    
    def test_cache_update_existing_entry(self):
        """기존 항목 업데이트 테스트"""
        cache = LRUEmbeddingCache(max_size=10)
        
        cache.put("text", [0.1])
        cache.put("text", [0.2])  # 업데이트
        
        result = cache.get("text")
        
        assert result == [0.2]
        assert len(cache) == 1


# =============================================================================
# RAGAgent 임베딩 생성 테스트
# =============================================================================

class TestRAGAgentEmbedding:
    """RAG Agent 임베딩 생성 테스트"""
    
    @pytest.fixture
    def mock_bedrock_client(self):
        """Mock Bedrock 클라이언트"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.__getitem__ = MagicMock(return_value=MagicMock())
        mock_response["body"].read.return_value = (
            '{"embedding": [' + ','.join(['0.1'] * EMBEDDING_DIMENSION) + ']}'
        ).encode()
        mock_client.invoke_model.return_value = mock_response
        return mock_client
    
    @pytest.fixture
    def rag_agent(self, mock_bedrock_client):
        """RAG Agent 인스턴스"""
        with patch('boto3.client', return_value=mock_bedrock_client):
            agent = RAGAgent(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                opensearch_endpoint=None
            )
            agent._bedrock_client = mock_bedrock_client
            return agent
    
    def test_generate_embedding_success(self, rag_agent, mock_bedrock_client):
        """임베딩 생성 성공 테스트"""
        result = rag_agent.generate_embedding("test query")
        
        assert result is not None
        assert len(result) == EMBEDDING_DIMENSION
        mock_bedrock_client.invoke_model.assert_called_once()
    
    def test_generate_embedding_empty_text(self, rag_agent):
        """빈 텍스트 임베딩 생성 테스트"""
        result = rag_agent.generate_embedding("")
        
        assert result is None
    
    def test_generate_embedding_whitespace_text(self, rag_agent):
        """공백 텍스트 임베딩 생성 테스트"""
        result = rag_agent.generate_embedding("   ")
        
        assert result is None
    
    def test_generate_embedding_uses_cache(self, rag_agent, mock_bedrock_client):
        """캐시 사용 테스트"""
        # 첫 번째 호출
        rag_agent.generate_embedding("test query")
        
        # 두 번째 호출 (캐시 히트)
        rag_agent.generate_embedding("test query")
        
        # Bedrock은 한 번만 호출되어야 함
        assert mock_bedrock_client.invoke_model.call_count == 1
    
    def test_generate_embedding_cache_bypass(self, rag_agent, mock_bedrock_client):
        """캐시 우회 테스트"""
        # 첫 번째 호출
        rag_agent.generate_embedding("test query", use_cache=True)
        
        # 두 번째 호출 (캐시 우회)
        rag_agent.generate_embedding("test query", use_cache=False)
        
        # Bedrock은 두 번 호출되어야 함
        assert mock_bedrock_client.invoke_model.call_count == 2
    
    def test_generate_embedding_no_bedrock_client(self):
        """Bedrock 클라이언트 없을 때 테스트"""
        with patch('boto3.client', side_effect=Exception("No client")):
            agent = RAGAgent(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                opensearch_endpoint=None
            )
            agent._bedrock_client = None
            
            result = agent.generate_embedding("test query")
            
            assert result is None
    
    def test_get_embedding_cache_stats(self, rag_agent):
        """캐시 통계 조회 테스트"""
        rag_agent.generate_embedding("test1")
        rag_agent.generate_embedding("test1")  # cache hit
        
        stats = rag_agent.get_embedding_cache_stats()
        
        assert "size" in stats
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate" in stats


# =============================================================================
# RAGAgent 재시도 로직 테스트
# =============================================================================

class TestRAGAgentRetry:
    """RAG Agent 재시도 로직 테스트"""
    
    def test_retry_on_transient_error(self):
        """일시적 에러 시 재시도 테스트"""
        mock_client = MagicMock()
        
        # 처음 2번 실패, 3번째 성공
        mock_response = MagicMock()
        mock_response["body"].read.return_value = (
            '{"embedding": [' + ','.join(['0.1'] * EMBEDDING_DIMENSION) + ']}'
        ).encode()
        
        mock_client.invoke_model.side_effect = [
            Exception("Throttling"),
            Exception("ServiceUnavailable"),
            mock_response
        ]
        
        with patch('boto3.client', return_value=mock_client):
            agent = RAGAgent(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                opensearch_endpoint=None
            )
            agent._bedrock_client = mock_client
            
            with patch('time.sleep'):  # 대기 시간 스킵
                result = agent.generate_embedding("test query", use_cache=False)
        
        assert result is not None
        assert mock_client.invoke_model.call_count == 3
    
    def test_no_retry_on_validation_error(self):
        """검증 에러 시 재시도 안함 테스트"""
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = Exception("ValidationException: Invalid input")
        
        with patch('boto3.client', return_value=mock_client):
            agent = RAGAgent(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                opensearch_endpoint=None
            )
            agent._bedrock_client = mock_client
            
            result = agent.generate_embedding("test query", use_cache=False)
        
        assert result is None
        # 재시도 없이 1번만 호출
        assert mock_client.invoke_model.call_count == 1
    
    def test_max_retry_attempts_exceeded(self):
        """최대 재시도 횟수 초과 테스트"""
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = Exception("ServiceUnavailable")
        
        with patch('boto3.client', return_value=mock_client):
            agent = RAGAgent(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                opensearch_endpoint=None
            )
            agent._bedrock_client = mock_client
            
            with patch('time.sleep'):  # 대기 시간 스킵
                result = agent.generate_embedding("test query", use_cache=False)
        
        assert result is None
        assert mock_client.invoke_model.call_count == MAX_RETRY_ATTEMPTS


# =============================================================================
# RAGAgent 상태 테스트
# =============================================================================

class TestRAGAgentStatus:
    """RAG Agent 상태 테스트"""
    
    def test_get_status_includes_cache_stats(self):
        """상태에 캐시 통계 포함 테스트"""
        with patch('boto3.client'):
            agent = RAGAgent(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                opensearch_endpoint=None
            )
            
            status = agent.get_status()
            
            assert "embedding_cache_stats" in status
            assert "size" in status["embedding_cache_stats"]
    
    def test_clear_embedding_cache(self):
        """임베딩 캐시 초기화 테스트"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response["body"].read.return_value = (
            '{"embedding": [' + ','.join(['0.1'] * EMBEDDING_DIMENSION) + ']}'
        ).encode()
        mock_client.invoke_model.return_value = mock_response
        
        with patch('boto3.client', return_value=mock_client):
            agent = RAGAgent(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                opensearch_endpoint=None
            )
            agent._bedrock_client = mock_client
            
            # 캐시에 항목 추가
            agent.generate_embedding("test1")
            agent.generate_embedding("test2")
            
            assert agent.get_embedding_cache_stats()["size"] == 2
            
            # 캐시 초기화
            agent.clear_embedding_cache()
            
            assert agent.get_embedding_cache_stats()["size"] == 0
