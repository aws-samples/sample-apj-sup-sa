"""Multi-Agent Text2SQL System

이 패키지는 Strands Graph 패턴을 사용하여 전문화된 에이전트들이 협업하는
멀티에이전트 text2sql 시스템을 구현합니다.

Components:
- Router Node: LLM 기반 요청 분류기 (2-way: data_query / general)
- Data Expert Agent: 데이터 카탈로그 탐색 전문가
- SQL Agent: 쿼리 생성/실행 전문가
- RAG Agent: 문서 검색 전문가 (OpenSearch 벡터 검색)
- Response Node: 최종 응답 포맷팅 에이전트
"""

from .data_expert_agent import DataExpertAgent
from .sql_agent import SQLAgent
from .rag_agent import (
    RAGAgent,
    SearchResult,
)
from .router_node import RouterNodeExecutor, RequestType, RouterResult
from .response_node import ResponseNode
from .multi_agent_text2sql import MultiAgentText2SQL
from .shared_context import AnalysisContext, TableInfo, ColumnInfo, GraphConfig
from .graph_conditions import (
    is_data_query,
    is_general_query,
    rag_completed,
    needs_sql,
    no_sql_needed,
    sql_succeeded,
    needs_more_tables,
    sql_max_retries,
)
from .event_adapter import (
    SwarmEventAdapter,
    SwarmEventHandler,
    StreamlitSwarmUIHandler,
    SwarmEventType,
    StreamlitEventType,
    AgentStatusInfo,
    SwarmEventAdapterState,
)

__all__ = [
    # Agents
    "DataExpertAgent",
    "SQLAgent",
    "RAGAgent",
    # Graph nodes
    "RouterNodeExecutor",
    "RequestType",
    "RouterResult",
    "ResponseNode",
    # Orchestrator
    "MultiAgentText2SQL",
    # Data models
    "AnalysisContext",
    "TableInfo",
    "ColumnInfo",
    "GraphConfig",
    # Graph conditions
    "is_data_query",
    "is_general_query",
    "rag_completed",
    "needs_sql",
    "no_sql_needed",
    "sql_succeeded",
    "needs_more_tables",
    "sql_max_retries",
    # RAG Agent
    "SearchResult",
    # Event Adapter
    "SwarmEventAdapter",
    "SwarmEventHandler",
    "StreamlitSwarmUIHandler",
    "SwarmEventType",
    "StreamlitEventType",
    "AgentStatusInfo",
    "SwarmEventAdapterState",
]
