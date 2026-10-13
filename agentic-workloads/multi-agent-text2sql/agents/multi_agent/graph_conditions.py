"""Graph 조건부 엣지 함수

GraphBuilder의 조건부 엣지에서 사용되는 함수들을 정의합니다.
각 함수는 GraphState를 받아 bool을 반환합니다.
"""

import logging

from strands.multiagent.base import Status

from .shared_context import GraphConfig

logger = logging.getLogger(__name__)

_MAX_DATA_EXPERT_RETRIES = GraphConfig.max_data_expert_loops


# ---------------------------------------------------------------------------
# Router 분기 조건 (router 노드 이후, 2-way)
# ---------------------------------------------------------------------------
# is_data_query   ──→ rag_node (데이터 파이프라인)
# is_general_query ──→ response_node (직행)
# ---------------------------------------------------------------------------

def _get_router_type(state) -> str:
    """Router 결과에서 request_type 문자열 추출

    invocation_state에 저장된 RouterResult 객체를 직접 참조합니다.
    str(AgentResult) 파싱에 의존하지 않습니다.
    """
    # invocation_state에서 직접 RouterResult 참조 (RouterNodeExecutor가 저장)
    inv_state = getattr(state, "invocation_state", None) or {}
    router_result = inv_state.get("router_result")
    if router_result and hasattr(router_result, "request_type"):
        return router_result.request_type.value

    # fallback: NodeResult.result 텍스트에서 추출
    node_result = state.results.get("router")
    if not node_result:
        return ""
    return str(node_result.result)


def is_data_query(state) -> bool:
    """Router 결과가 데이터 조회인지 확인"""
    result_text = _get_router_type(state)
    matched = "data_query" in result_text
    logger.debug("is_data_query=%s", matched)
    return matched


def is_general_query(state) -> bool:
    """Router 결과가 일반 대화(데이터 작업 불필요)인지 확인

    data_query가 아닌 경우 True.
    인사, 잡담, 도움말 등 → response_node로 직행.
    """
    return not is_data_query(state)


# ---------------------------------------------------------------------------
# RAG → Data Expert 순차 실행 조건
# ---------------------------------------------------------------------------

def rag_completed(state) -> bool:
    """RAG 노드 완료 후 data_expert로 진행"""
    rag = state.results.get("rag_node")
    return rag is not None and rag.status == Status.COMPLETED


# ---------------------------------------------------------------------------
# DataExpert 분기 조건 (data_expert 노드 이후)
# ---------------------------------------------------------------------------
# data_expert 출력에서 [SQL_NEEDED] / [INFO_ONLY] 마커를 확인
# ---------------------------------------------------------------------------

def needs_sql(state) -> bool:
    """data_expert 출력에서 SQL 필요 여부 확인

    [INFO_ONLY] 마커가 있으면 SQL 불필요.
    마커가 없으면 기본값 True (안전한 fallback).
    """
    data_expert_result = state.results.get("data_expert")
    if not data_expert_result:
        return True

    result_text = str(data_expert_result.result)
    if "[INFO_ONLY]" in result_text:
        logger.debug("needs_sql=False ([INFO_ONLY] marker found)")
        return False

    logger.debug("needs_sql=True")
    return True


def no_sql_needed(state) -> bool:
    """SQL이 불필요한지 확인 (needs_sql의 논리적 역)"""
    return not needs_sql(state)


# ---------------------------------------------------------------------------
# SQL 분기 조건 (sql_node 이후)
# ---------------------------------------------------------------------------

def _get_sql_result_text(state) -> str:
    """sql_node 결과 텍스트를 안전하게 추출"""
    sql_result = state.results.get("sql_node")
    if not sql_result or sql_result.status != Status.COMPLETED:
        return ""
    return str(sql_result.result)


def _count_data_expert_executions(state) -> int:
    """data_expert 노드 실행 횟수를 카운트"""
    return sum(
        1 for node in state.execution_order if node.node_id == "data_expert"
    )


_SQL_ERROR_PATTERNS = ["FAILED", "오류", "에러", "실패", "table_missing"]
_TABLE_MISSING_PATTERNS = ["table_missing", "Table not found"]


def sql_succeeded(state) -> bool:
    """SQL 노드가 성공적으로 완료되었는지 확인 (에러 없음)"""
    sql_result = state.results.get("sql_node")
    if not sql_result:
        return False
    if sql_result.status != Status.COMPLETED:
        return False

    result_text = str(sql_result.result)
    has_error = any(p in result_text for p in _SQL_ERROR_PATTERNS)
    succeeded = not has_error
    logger.debug("sql_succeeded=%s", succeeded)
    return succeeded


def needs_more_tables(state) -> bool:
    """SQL 에러가 테이블 누락이고 재시도 가능한지 확인"""
    result_text = _get_sql_result_text(state)
    if not result_text:
        return False

    is_table_missing = any(p in result_text for p in _TABLE_MISSING_PATTERNS)
    exec_count = _count_data_expert_executions(state)
    within_limit = exec_count < _MAX_DATA_EXPERT_RETRIES

    needs_retry = is_table_missing and within_limit
    logger.debug(
        "needs_more_tables=%s (table_missing=%s, data_expert_count=%d)",
        needs_retry, is_table_missing, exec_count,
    )
    return needs_retry


def sql_max_retries(state) -> bool:
    """SQL 재시도 한도 초과 또는 기타 에러인지 확인

    sql_result가 없으면 치명적 실패로 간주하여 response_node로 보냅니다.
    """
    sql_result = state.results.get("sql_node")
    if not sql_result:
        return True
    if sql_result.status != Status.COMPLETED:
        return True

    result_text = str(sql_result.result)
    # table_missing 제외한 에러 패턴만 확인 (table_missing은 needs_more_tables에서 처리)
    has_error = any(
        p in result_text
        for p in _SQL_ERROR_PATTERNS
    )

    if not has_error:
        return False

    # table_missing이면서 재시도 가능하면 needs_more_tables가 처리
    is_table_missing = any(p in result_text for p in _TABLE_MISSING_PATTERNS)
    if is_table_missing and _count_data_expert_executions(state) < _MAX_DATA_EXPERT_RETRIES:
        return False

    logger.debug("sql_max_retries=True")
    return True


# ---------------------------------------------------------------------------
# Cache Node 분기 조건
# ---------------------------------------------------------------------------
