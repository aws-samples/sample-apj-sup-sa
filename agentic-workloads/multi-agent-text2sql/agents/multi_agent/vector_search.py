"""벡터 검색 서비스

OpenSearch 벡터 검색과 Bedrock 임베딩 생성을 담당합니다.
"""

import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 상수
DEFAULT_OPENSEARCH_INDEX = "schema_docs"
DEFAULT_TOP_K = 5
DEFAULT_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSION = 1024
MAX_EMBEDDING_TOKENS = 8192
OPENSEARCH_TIMEOUT = 30
EMBEDDING_TIMEOUT = 30
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1
RETRY_BACKOFF_MULTIPLIER = 2.0
EMBEDDING_CACHE_MAX_SIZE = 100
EMBEDDING_CACHE_TTL_SECONDS = 3600


@dataclass
class SearchResult:
    """벡터 검색 결과"""
    content: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = ""


@dataclass
class EmbeddingCacheEntry:
    """임베딩 캐시 엔트리"""
    embedding: List[float]
    created_at: float
    text_hash: str


class LRUEmbeddingCache:
    """LRU 기반 임베딩 캐시"""

    def __init__(self, max_size: int = EMBEDDING_CACHE_MAX_SIZE, ttl_seconds: int = EMBEDDING_CACHE_TTL_SECONDS):
        self._cache: OrderedDict[str, EmbeddingCacheEntry] = OrderedDict()
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._hits = 0
        self._misses = 0

    def _compute_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]

    def get(self, text: str) -> Optional[List[float]]:
        text_hash = self._compute_hash(text)
        if text_hash not in self._cache:
            self._misses += 1
            return None
        entry = self._cache[text_hash]
        if time.time() - entry.created_at > self._ttl_seconds:
            del self._cache[text_hash]
            self._misses += 1
            return None
        self._cache.move_to_end(text_hash)
        self._hits += 1
        return entry.embedding

    def put(self, text: str, embedding: List[float]) -> None:
        text_hash = self._compute_hash(text)
        if text_hash in self._cache:
            self._cache.move_to_end(text_hash)
            self._cache[text_hash] = EmbeddingCacheEntry(embedding=embedding, created_at=time.time(), text_hash=text_hash)
            return
        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
        self._cache[text_hash] = EmbeddingCacheEntry(embedding=embedding, created_at=time.time(), text_hash=text_hash)

    def clear(self) -> None:
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def get_stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {"size": len(self._cache), "hits": self._hits, "misses": self._misses, "hit_rate": self._hits / total if total > 0 else 0.0}


class VectorSearchService:
    """벡터 검색 서비스 - OpenSearch + Bedrock 임베딩"""

    def __init__(
        self,
        opensearch_endpoint: Optional[str] = None,
        opensearch_index: str = DEFAULT_OPENSEARCH_INDEX,
        opensearch_username: Optional[str] = None,
        opensearch_password: Optional[str] = None,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL
    ):
        self.opensearch_endpoint = opensearch_endpoint
        self.opensearch_index = opensearch_index
        self.opensearch_username = opensearch_username
        self.opensearch_password = opensearch_password
        self.embedding_model = embedding_model

        self._opensearch_client = None
        self._bedrock_client = None
        self._enabled = True
        self._embedding_cache = LRUEmbeddingCache()

        self._init_clients()

    def _init_clients(self) -> None:
        """클라이언트 초기화"""
        # Bedrock
        try:
            import boto3
            region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
            self._bedrock_client = boto3.client("bedrock-runtime", region_name=region)
            logger.info(f"Bedrock 클라이언트 초기화 성공 (region: {region})")
        except Exception as e:
            logger.warning(f"Bedrock 초기화 실패: {e}")
            self._enabled = False

        # OpenSearch
        if self.opensearch_endpoint:
            try:
                from opensearchpy import OpenSearch, RequestsHttpConnection
                host = self.opensearch_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
                port = 443
                if ":" in host:
                    host, port_str = host.rsplit(":", 1)
                    port = int(port_str)

                if ".aoss.amazonaws.com" in self.opensearch_endpoint:
                    # OpenSearch Serverless — SigV4 인증
                    # 엔드포인트 URL에서 리전 추출: https://<id>.<region>.aoss.amazonaws.com
                    import re
                    from requests_aws4auth import AWS4Auth
                    region_match = re.search(r"\.([a-z0-9-]+)\.aoss\.amazonaws\.com", self.opensearch_endpoint)
                    region = region_match.group(1) if region_match else os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
                    credentials = boto3.Session(profile_name="default").get_credentials().get_frozen_credentials()
                    awsauth = AWS4Auth(
                        credentials.access_key, credentials.secret_key,
                        region, "aoss", session_token=credentials.token,
                    )
                    self._opensearch_client = OpenSearch(
                        hosts=[{"host": host, "port": port}],
                        http_auth=awsauth,
                        use_ssl=True, verify_certs=True,
                        connection_class=RequestsHttpConnection, timeout=OPENSEARCH_TIMEOUT
                    )
                    logger.info(f"OpenSearch Serverless 초기화 성공 (SigV4): {host}")
                elif self.opensearch_username and self.opensearch_password:
                    # 자체 호스팅 OpenSearch — basic auth
                    is_localhost = host in ("localhost", "127.0.0.1")
                    self._opensearch_client = OpenSearch(
                        hosts=[{"host": host, "port": port}],
                        http_auth=(self.opensearch_username, self.opensearch_password),
                        use_ssl=True, verify_certs=not is_localhost, ssl_show_warn=False,
                        connection_class=RequestsHttpConnection, timeout=OPENSEARCH_TIMEOUT
                    )
                    logger.info(f"OpenSearch 초기화 성공 (basic auth): {host}:{port}")
                else:
                    logger.info("OpenSearch 인증 정보 없음 - 검색 비활성화")
                    self._enabled = False
            except Exception as e:
                logger.warning(f"OpenSearch 초기화 실패: {e}")
                self._enabled = False
        else:
            logger.info("OpenSearch 설정 미완료 - 검색 비활성화")
            self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def generate_embedding(self, text: str, use_cache: bool = True) -> Optional[List[float]]:
        """임베딩 생성"""
        if not text or not text.strip() or not self._bedrock_client:
            return None

        if use_cache:
            cached = self._embedding_cache.get(text)
            if cached:
                return cached

        embedding = self._generate_embedding_with_retry(text)
        if embedding and use_cache:
            self._embedding_cache.put(text, embedding)
        return embedding

    def _generate_embedding_with_retry(self, text: str) -> Optional[List[float]]:
        delay = RETRY_DELAY_SECONDS
        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            try:
                max_chars = MAX_EMBEDDING_TOKENS * 4
                if len(text) > max_chars:
                    text = text[:max_chars]
                body = json.dumps({"inputText": text, "dimensions": EMBEDDING_DIMENSION, "normalize": True})
                response = self._bedrock_client.invoke_model(
                    modelId=self.embedding_model, body=body, contentType="application/json", accept="application/json"
                )
                response_body = json.loads(response["body"].read())
                return response_body.get("embedding", []) or None
            except Exception as e:
                if attempt < MAX_RETRY_ATTEMPTS:
                    time.sleep(delay)
                    delay *= RETRY_BACKOFF_MULTIPLIER
                else:
                    logger.error(f"임베딩 생성 실패: {e}")
        return None

    def search(self, query: str, top_k: int = DEFAULT_TOP_K, filters: Optional[Dict[str, str]] = None) -> Tuple[List[SearchResult], Optional[str]]:
        """벡터 검색 수행"""
        if not self._enabled or not self._opensearch_client:
            return [], "검색 서비스가 비활성화되어 있습니다"

        top_k = min(top_k, DEFAULT_TOP_K)
        query_embedding = self.generate_embedding(query)
        if not query_embedding:
            return [], "임베딩 생성 실패"

        return self._search_with_retry(query_embedding, query, top_k, filters)

    def _search_with_retry(self, embedding: List[float], query_text: str, top_k: int, filters: Optional[Dict[str, str]]) -> Tuple[List[SearchResult], Optional[str]]:
        delay = RETRY_DELAY_SECONDS
        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            try:
                search_query = self._build_query(embedding, query_text, top_k, filters)
                logger.info(f"[OpenSearch] 인덱스: {self.opensearch_index}, 쿼리: '{query_text}', top_k: {top_k}")

                response = self._opensearch_client.search(index=self.opensearch_index, body=search_query, request_timeout=OPENSEARCH_TIMEOUT)
                results = self._parse_results(response)

                hits_total = response.get("hits", {}).get("total", {})
                total = hits_total.get("value", 0) if isinstance(hits_total, dict) else hits_total
                logger.info(f"[OpenSearch] 검색 완료: {len(results)}개 반환 (전체 {total}개)")
                return results, None
            except Exception as e:
                if attempt < MAX_RETRY_ATTEMPTS:
                    time.sleep(delay)
                    delay *= RETRY_BACKOFF_MULTIPLIER
                else:
                    return [], f"검색 실패: {e}"
        return [], "검색 실패"

    def _build_query(self, embedding: List[float], query_text: str, top_k: int, filters: Optional[Dict[str, str]]) -> Dict[str, Any]:
        knn_query = {"knn": {"embedding": {"vector": embedding, "k": top_k}}}
        keyword_query = {"multi_match": {"query": query_text, "fields": ["content", "table", "database", "column_name"], "type": "best_fields", "fuzziness": "AUTO"}}
        bool_query: Dict[str, Any] = {"should": [knn_query, keyword_query], "minimum_should_match": 1}
        if filters:
            filter_clauses = []
            if "database" in filters:
                filter_clauses.append({"term": {"database": filters["database"]}})
            if "table" in filters:
                filter_clauses.append({"term": {"table": filters["table"]}})
            if filter_clauses:
                bool_query["filter"] = filter_clauses
        return {"size": top_k, "query": {"bool": bool_query}, "_source": ["content", "table", "database", "chunk_type", "column_name", "source_file"]}

    def _parse_results(self, response: Dict[str, Any]) -> List[SearchResult]:
        results = []
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            score = min(hit.get("_score", 0.0) / 10.0, 1.0)
            results.append(SearchResult(
                content=source.get("content", ""),
                score=score,
                metadata={"table": source.get("table", ""), "database": source.get("database", ""), "chunk_type": source.get("chunk_type", ""), "column_name": source.get("column_name", "")},
                source=source.get("source_file", hit.get("_id", ""))
            ))
        return results

    def get_alternative_suggestions(self, query: str) -> List[Dict[str, str]]:
        """검색 결과 없을 때 대안 제안"""
        suggestions = []
        words = query.lower().split()
        if len(words) > 1:
            suggestions.append({"type": "split", "suggestion": f"개별 키워드로 검색: {', '.join(words[:3])}"})
        suggestions.append({"type": "browse", "suggestion": "전체 카탈로그 탐색"})
        return suggestions[:5]

    def get_cache_stats(self) -> Dict[str, Any]:
        return self._embedding_cache.get_stats()

    def clear_cache(self) -> None:
        self._embedding_cache.clear()
