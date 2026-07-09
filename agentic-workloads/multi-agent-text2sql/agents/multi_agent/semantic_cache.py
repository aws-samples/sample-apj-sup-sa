"""Semantic Cache - ElastiCache Valkey 벡터 검색 기반 시맨틱 캐시

유사한 질문에 대해 이전 결과를 재사용하여 지연 시간과 비용을 절감합니다.
ElastiCache Valkey 8.2의 벡터 검색(FT.CREATE / FT.SEARCH)을 사용합니다.
"""

import hashlib
import json
import logging
import os
import struct
import time
from typing import Any, List, Optional

import valkey

logger = logging.getLogger(__name__)

# 기존 vector_search.py와 동일한 임베딩 설정
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSION = 1024


class SemanticCache:
    """ElastiCache Valkey 벡터 검색 기반 Semantic Cache

    Args:
        namespace: 캐시 용도별 분리 키 (예: "rag", "llm")
        threshold: 코사인 유사도 임계값 (기본 0.90)
        ttl_seconds: 캐시 만료 시간 (기본 3600초)
    """

    def __init__(
        self,
        namespace: str,
        threshold: float = 0.90,
        ttl_seconds: int = 3600,
    ):
        self.namespace = namespace
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds

        # ElastiCache 연결
        host = os.environ.get("VALKEY_ENDPOINT", "localhost")
        port = int(os.environ.get("VALKEY_PORT", "6379"))
        self._client = valkey.Valkey(host=host, port=port, decode_responses=False)

        # Bedrock 클라이언트 (임베딩 생성용) — vector_search.py와 동일한 리전 로직
        import boto3
        region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        self._bedrock = boto3.client("bedrock-runtime", region_name=region)

        self._ensure_index()

    def _ensure_index(self):
        """HNSW 벡터 인덱스가 없으면 생성"""
        index_name = f"idx:{self.namespace}"
        try:
            self._client.execute_command("FT.INFO", index_name)
        except Exception:
            self._client.execute_command(
                "FT.CREATE", index_name,
                "ON", "HASH",
                "PREFIX", "1", f"{self.namespace}:",
                "SCHEMA",
                "embedding", "VECTOR", "HNSW", "6",
                    "TYPE", "FLOAT32",
                    "DIM", str(EMBEDDING_DIMENSION),
                    "DISTANCE_METRIC", "COSINE",
            )
            logger.info(f"[SemanticCache] 인덱스 '{index_name}' 생성 완료")

    def _generate_embedding(self, text: str) -> List[float]:
        """Bedrock Titan으로 임베딩 생성 — vector_search.py와 동일한 모델/파라미터"""
        body = json.dumps({
            "inputText": text,
            "dimensions": EMBEDDING_DIMENSION,
            "normalize": True,
        })
        response = self._bedrock.invoke_model(
            modelId=EMBEDDING_MODEL,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        return json.loads(response["body"].read())["embedding"]

    def get(self, query: str) -> Optional[Any]:
        """캐시에서 유사한 질문의 결과를 검색

        Returns:
            캐시 히트 시 저장된 결과, 미스 시 None
        """
        try:
            _start = time.time()
            embedding = self._generate_embedding(query)
            blob = struct.pack(f"{len(embedding)}f", *embedding)

            results = self._client.execute_command(
                "FT.SEARCH", f"idx:{self.namespace}",
                "(*)=>[KNN 1 @embedding $vec AS score]",
                "PARAMS", "2", "vec", blob,
                "LIMIT", "0", "1",
                "DIALECT", "2",
            )

            if results[0] == 0:
                _elapsed = (time.time() - _start) * 1000
                logger.info(f"[SemanticCache:{self.namespace}] MISS - 캐시 비어있음 ({_elapsed:.0f}ms)")
                return {"miss": True, "similarity": 0, "elapsed_ms": _elapsed}

            # 결과 파싱: [count, key, [field, value, ...]]
            fields = results[2]
            score = None
            data = None
            for i in range(0, len(fields), 2):
                name = fields[i].decode() if isinstance(fields[i], bytes) else fields[i]
                if name == "score":
                    score = float(fields[i + 1])
                elif name == "data":
                    raw = fields[i + 1]
                    data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)

            # 코사인 거리 → 유사도 (1 - distance)
            similarity = 1.0 - score if score is not None else 0.0

            if similarity >= self.threshold and data is not None:
                _elapsed = (time.time() - _start) * 1000
                logger.info(f"[SemanticCache:{self.namespace}] HIT - 유사도: {similarity:.4f} ({_elapsed:.0f}ms)")
                # 캐시된 질문 텍스트도 파싱
                cached_query = None
                for i in range(0, len(fields), 2):
                    name = fields[i].decode() if isinstance(fields[i], bytes) else fields[i]
                    if name == "query":
                        raw = fields[i + 1]
                        cached_query = raw.decode() if isinstance(raw, bytes) else raw
                return {"data": data, "similarity": similarity, "cached_query": cached_query, "elapsed_ms": _elapsed}

            _elapsed = (time.time() - _start) * 1000
            logger.info(f"[SemanticCache:{self.namespace}] MISS - 유사도: {similarity:.4f} < {self.threshold} ({_elapsed:.0f}ms)")
            return {"miss": True, "similarity": similarity, "elapsed_ms": _elapsed}

        except Exception as e:
            logger.warning(f"[SemanticCache:{self.namespace}] 검색 실패: {e}")
            return {"miss": True, "similarity": 0, "elapsed_ms": 0, "error": str(e)}

    def set(self, query: str, data: Any) -> None:
        """결과를 캐시에 저장"""
        try:
            embedding = self._generate_embedding(query)
            blob = struct.pack(f"{len(embedding)}f", *embedding)

            key = f"{self.namespace}:{hashlib.sha256(query.encode()).hexdigest()[:16]}"
            self._client.hset(key, mapping={
                "embedding": blob,
                "data": json.dumps(data, ensure_ascii=False),
                "query": query,
                "created_at": str(time.time()),
            })
            if self.ttl_seconds > 0:
                self._client.expire(key, self.ttl_seconds)

            logger.info(f"[SemanticCache:{self.namespace}] 저장 완료")

        except Exception as e:
            logger.warning(f"[SemanticCache:{self.namespace}] 저장 실패: {e}")
