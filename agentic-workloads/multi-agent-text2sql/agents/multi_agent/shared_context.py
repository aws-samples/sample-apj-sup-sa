"""공유 컨텍스트 및 데이터 모델

멀티에이전트 시스템에서 사용되는 공유 데이터 구조와 설정을 정의합니다.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ColumnInfo:
    """테이블 컬럼 정보"""
    name: str
    type: str
    description: Optional[str] = None


@dataclass
class TableInfo:
    """테이블 메타데이터 정보"""
    database: str
    table: str
    columns: List[ColumnInfo] = field(default_factory=list)
    partition_keys: List[str] = field(default_factory=list)
    relevance_score: float = 0.0


@dataclass
class AnalysisContext:
    """에이전트 간 공유되는 분석 컨텍스트"""
    user_query: str = ""
    business_intent: Dict[str, Any] = field(default_factory=dict)  # entity, metric, time, action
    identified_tables: List[TableInfo] = field(default_factory=list)
    generated_sql: Optional[str] = None
    query_execution_id: Optional[str] = None
    results: Optional[List[Dict]] = None
    error_messages: List[str] = field(default_factory=list)
    
    # RAG 관련 필드
    rag_results: List[Dict[str, Any]] = field(default_factory=list)
    rag_enabled: bool = True  # RAG 사용 여부

    # Graph 패턴 관련 필드
    request_type: str = "data_query"  # Router 분류 결과 ("data_query" | "general")

    def add_error(self, message: str):
        """에러 메시지 추가"""
        self.error_messages.append(message)

    def clear_errors(self):
        """에러 메시지 초기화"""
        self.error_messages.clear()

    def add_rag_result(self, result: Dict[str, Any]) -> None:
        """RAG 검색 결과 추가"""
        self.rag_results.append(result)

    def clear_rag_results(self) -> None:
        """RAG 검색 결과 초기화"""
        self.rag_results.clear()


@dataclass
class GraphConfig:
    """Graph 패턴 설정"""
    max_node_executions: int = 15
    execution_timeout: float = 900.0   # 15분
    node_timeout: float = 300.0        # 5분
    reset_on_revisit: bool = True
    max_sql_retries: int = 2
    max_data_expert_loops: int = 2