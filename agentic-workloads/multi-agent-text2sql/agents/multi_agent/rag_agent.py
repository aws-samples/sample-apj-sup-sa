"""RAG Agent - 문서 검색 전문가

OpenSearch 벡터 데이터베이스에서 스키마 문서와 도메인 지식을 검색하여
다른 에이전트에게 컨텍스트를 제공하는 전문가입니다.
"""

import logging
import sys
from typing import Any, Dict, List, Optional

from strands import Agent
from strands.tools import tool

from .base_agent import BaseMultiAgent
from .shared_context import AnalysisContext
from .vector_search import VectorSearchService, SearchResult

logger = logging.getLogger(__name__)


class RAGAgent(BaseMultiAgent):
    """RAG Agent - 문서 검색 전문가"""

    def __init__(
        self,
        model_id: str,
        opensearch_endpoint: Optional[str] = None,
        opensearch_index: str = "schema_docs",
        embedding_model: str = "amazon.titan-embed-text-v2:0",
        opensearch_username: Optional[str] = None,
        opensearch_password: Optional[str] = None,
        tools: Optional[List] = None
    ):
        self.opensearch_endpoint = opensearch_endpoint
        self.opensearch_index = opensearch_index
        self.embedding_model = embedding_model
        self.opensearch_username = opensearch_username
        self.opensearch_password = opensearch_password

        # 벡터 검색 서비스 초기화
        self._search_service = VectorSearchService(
            opensearch_endpoint=opensearch_endpoint,
            opensearch_index=opensearch_index,
            opensearch_username=opensearch_username,
            opensearch_password=opensearch_password,
            embedding_model=embedding_model
        )

        super().__init__(model_id, tools)

    def _create_search_tools(self) -> List:
        """RAG 검색 도구 생성"""
        rag_agent = self

        @tool
        def search_rag_documents(query: str) -> str:
            """RAG 문서 검색 도구

            스키마 문서와 도메인 지식을 검색합니다.

            Args:
                query: 검색할 쿼리

            Returns:
                검색된 문서 정보
            """
            logger.info(f"[RAG 검색] 쿼리: '{query}'")

            import time
            _search_start = time.time()
            result = rag_agent.search_and_extract(query)
            _search_elapsed = (time.time() - _search_start) * 1000

            if not result["success"]:
                errors = ", ".join(result["errors"]) if result["errors"] else "알 수 없는 오류"
                logger.warning(f"[RAG 검색] 실패: {errors}")
                return f"검색 실패: {errors}"

            if result["results"]:
                print(f"\n📊 [RAG 검색] 문서 {len(result['results'])}개 발견 (소요시간: {_search_elapsed:.0f}ms)", file=sys.stderr)
                for i, doc in enumerate(result["results"], 1):
                    print(f"  [{i}] score={doc.score:.3f} | table={doc.metadata.get('table', 'N/A')} | db={doc.metadata.get('database', 'N/A')}", file=sys.stderr)
                output = "## 검색 결과\n" + rag_agent.format_results_for_agent(result["results"])
                logger.info(f"[RAG 검색] 결과 반환 (길이: {len(output)}자)")
                return output

            logger.warning(f"[RAG 검색] 결과 없음: '{query}'")
            if result["suggestions"]:
                suggestions_text = "\n".join([f"- {s['suggestion']}" for s in result["suggestions"][:3]])
                return f"'{query}'에 대한 검색 결과가 없습니다.\n\n대안 제안:\n{suggestions_text}"
            return f"'{query}'에 대한 검색 결과가 없습니다."

        return [search_rag_documents]

    def _setup_agent(self) -> None:
        """RAG Agent 초기화"""
        search_tools = self._create_search_tools()
        all_tools = search_tools + (self.tools if self.tools else [])

        self.agent = Agent(
            name="rag_agent",
            system_prompt=self.get_system_prompt(),
            model=self.model_id,
            tools=all_tools if all_tools else None
        )

    def get_system_prompt(self) -> str:
        """RAG Agent 시스템 프롬프트"""
        return """
역할: 벡터 데이터베이스 문서 검색 전문가

────────────────────────────────────────────
검색 규칙 (필수)
────────────────────────────────────────────
- search_rag_documents 호출은 최대 2회까지만 허용
- 1회 검색으로 관련 테이블을 찾았으면 추가 검색하지 않음
- 2회째는 1회 결과가 부족한 경우에만 다른 키워드로 검색

────────────────────────────────────────────
주요 기능
────────────────────────────────────────────

1. **스키마 문서 검색**
   - 사용자 쿼리와 관련된 테이블/컬럼 정보 검색
   - 테이블명, 컬럼명, 데이터 타입, 설명 추출

2. **도메인 지식 검색**
   - 비즈니스 용어 → 데이터베이스 컬럼 매핑
   - 메트릭 정의 및 계산 공식 검색
   - 값 설명 및 비즈니스 로직 검색

────────────────────────────────────────────
검색 결과 형식
────────────────────────────────────────────

각 검색 결과에 포함:
- content: 문서 내용
- score: 관련도 점수 (0.0 ~ 1.0)
- metadata: 테이블명, 데이터베이스명 등
- source: 출처 문서 경로

────────────────────────────────────────────
결과 반환 규칙 (중요!)
────────────────────────────────────────────

검색 완료 후 **발견한 사실만 보고**합니다.
쿼리 가능/불가능 판단, 권고사항, 문제점 분석은 절대 하지 않습니다.

반환 형식:
```
[원문 질문] (사용자 질문 그대로)

[발견된 테이블]
- database.table: 설명
- 컬럼: column_name (타입) - 설명

[도메인 지식]
- 비즈니스 용어 → 컬럼/값 매핑 (검색에서 발견된 것만)

[조인 관계]
- table_a.key → table_b.key

[샘플 쿼리] (해당되는 경우만 — 없으면 이 섹션 생략)
현재 질문의 SQL 패턴(비율 계산, 비교, 집계 방식 등)과 동일한 샘플 쿼리가 있을 때만 원문 그대로 복사합니다.
패턴이 다른 샘플 쿼리는 포함하지 않습니다.
SQL, 조건, 부등호를 절대 수정하거나 재해석하지 않으며, 적용 방법에 대한 설명도 붙이지 않습니다.
```

금지 사항:
- "이 쿼리는 불가능합니다" 등 실현 가능성 판단
- "추가 테이블이 필요합니다" 등 권고사항
- "❌ 없음", "⚠️ 부분" 등 부정적 평가

────────────────────────────────────────────
오류 처리
────────────────────────────────────────────

- OpenSearch 연결 실패 → 오류 내용을 결과에 포함
- 검색 결과 없음 → 대안 검색어 제안
- 임베딩 생성 실패 → 오류 내용을 결과에 포함
"""

    def get_tools(self) -> List:
        return self.tools

    def get_agent(self) -> Agent:
        if not self.agent:
            raise RuntimeError("Agent not initialized")
        return self.agent

    def search_and_extract(self, query: str, context: Optional[AnalysisContext] = None) -> Dict[str, Any]:
        """검색 및 정보 추출 통합 메서드"""
        result = {
            "success": True,
            "rag_enabled": self._search_service.is_enabled(),
            "results": [],
            "suggestions": [],
            "errors": []
        }

        if not self._search_service.is_enabled():
            result["success"] = False
            result["errors"].append("RAG가 비활성화되어 있습니다")
            return result

        try:
            search_results, search_error = self._search_service.search(query)
            if search_error:
                result["errors"].append(f"검색 실패: {search_error}")
            else:
                result["results"] = search_results
                if not search_results:
                    result["suggestions"] = self._search_service.get_alternative_suggestions(query)

            # 컨텍스트에 저장
            if context and search_results:
                self.save_to_context(context, search_results)

            result["success"] = len(result["errors"]) == 0

        except Exception as e:
            result["success"] = False
            result["errors"].append(f"검색 중 예외: {e}")

        return result

    def format_results_for_agent(self, search_results: List[SearchResult]) -> str:
        """검색 결과를 LLM 친화적 형식으로 변환"""
        if not search_results:
            return "검색 결과가 없습니다."

        lines = []
        for i, result in enumerate(search_results[:5], 1):
            lines.append(f"### {i}. (관련도: {result.score:.3f})")
            if result.metadata:
                meta = result.metadata
                if meta.get("database"):
                    lines.append(f"- DB: `{meta['database']}`")
                if meta.get("table"):
                    lines.append(f"- 테이블: `{meta['table']}`")
                if meta.get("column_name"):
                    lines.append(f"- 컬럼: `{meta['column_name']}`")
            lines.append(f"\n{result.content}\n")
            if result.source:
                lines.append(f"출처: `{result.source}`\n")
            lines.append("---")

        return "\n".join(lines)

    def save_to_context(
        self,
        context: AnalysisContext,
        search_results: Optional[List[SearchResult]] = None
    ) -> AnalysisContext:
        """검색 결과를 공유 컨텍스트에 저장"""
        if search_results:
            for result in search_results:
                context.add_rag_result({
                    "content": result.content[:500],
                    "score": result.score,
                    "metadata": result.metadata,
                    "source": result.source
                })
        return context

    def is_rag_enabled(self) -> bool:
        return self._search_service.is_enabled()

    def get_status(self) -> Dict[str, Any]:
        return {
            "rag_enabled": self._search_service.is_enabled(),
            "opensearch_endpoint": self.opensearch_endpoint,
            "opensearch_index": self.opensearch_index,
            "cache_stats": self._search_service.get_cache_stats()
        }
