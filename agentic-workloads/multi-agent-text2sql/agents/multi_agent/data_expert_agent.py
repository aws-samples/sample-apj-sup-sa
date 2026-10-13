"""Data Expert Agent - 데이터 카탈로그 탐색 전문가 (LLM 기반)

AWS Athena 데이터 카탈로그를 탐색하고 LLM을 통해 비즈니스 요구사항에 적합한 테이블을 식별합니다.
"""

from typing import List, Optional

from strands import Agent

from .base_agent import BaseMultiAgent


class DataExpertAgent(BaseMultiAgent):
    """Data Expert Agent - 데이터 카탈로그 탐색 전문가 (LLM 기반)

    Graph에서 MCP 도구를 사용하여 직접 카탈로그를 탐색하고,
    LLM이 테이블 스키마를 분석하여 적합한 테이블을 추천합니다.
    """

    def __init__(self, model_id: str, tools: Optional[List] = None):
        super().__init__(model_id, tools)

    def _setup_agent(self):
        """Data Expert Agent 초기화"""
        self.agent = Agent(
            name="data_expert",
            system_prompt=self.get_system_prompt(),
            model=self.model_id,
            tools=self.tools if self.tools else None
        )

    def get_system_prompt(self) -> str:
        """Data Expert Agent 시스템 프롬프트 (LLM 기반 테이블 매칭)"""
        return """
역할: AWS Athena 데이터 카탈로그 탐색 전문가

MCP 도구를 사용하여 데이터 카탈로그를 탐색하고 결과를 반환합니다.

────────────────────────────────────────────
작업 절차
────────────────────────────────────────────

대화에 RAG 검색 결과가 포함되어 있으므로, RAG 결과에서 식별된 database.table을 사용합니다.

1. get_table → RAG 결과에서 언급된 테이블의 컬럼명 + 타입 상세 조회
2. (1에서 부족한 경우만) list_tables → 같은 DB 내 추가 테이블 탐색

list_databases는 호출하지 않습니다.

────────────────────────────────────────────
결과 출력 형식 (필수)
────────────────────────────────────────────

RAG 결과의 정보를 모두 유지하고, 카탈로그에서 확인한 스키마 정보를 추가합니다.

[원문 질문] (RAG 결과에서 그대로 가져옴)

[도메인 지식] (RAG 결과에서 그대로 가져옴)

[발견된 스키마] (카탈로그에서 확인한 실제 컬럼 타입 추가)
테이블: database.table_name
컬럼:
- column_name (타입: bigint)
- date_column (타입: string, 형식: YYYY-MM-DD)  ← 날짜가 string이면 형식 명시
파티션 키: partition_col (타입)

[조인 관계] (RAG 결과에서 그대로 가져옴)

[컬럼 매핑]
- {사용자 표현} → {실제 컬럼명}

[샘플 쿼리] (RAG 결과에 포함된 경우 원문 그대로 가져옴)

금지 사항:
- RAG 결과의 도메인 지식, 조인 관계, 샘플 쿼리를 생략하거나 수정
- 쿼리 가능/불가능 판단, 권고사항, 부정적 평가

────────────────────────────────────────────
오류 처리
────────────────────────────────────────────
- MCP 도구 호출 실패 시 최대 3회 재시도
- 3회 실패 시 오류 내용을 결과에 포함하여 반환

────────────────────────────────────────────
라우팅 시그널 (필수)
────────────────────────────────────────────
사용자 요청이 데이터 조회/집계/비교라면 항상 [SQL_NEEDED]를 출력합니다.
- [SQL_NEEDED] — 데이터 조회, 집계, 비교 등 SQL 실행이 필요한 경우
- [INFO_ONLY] — 테이블 목록, 스키마 정보만 물어본 경우
"""

    def get_tools(self) -> List:
        """Data Expert Agent 도구 목록"""
        return self.tools
