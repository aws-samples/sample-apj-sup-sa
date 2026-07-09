"""SQL Agent - 쿼리 생성/실행 전문가 (LLM 기반)

Data Expert로부터 받은 카탈로그 정보와 사용자 자연어 쿼리를 기반으로
LLM이 MCP 도구를 통해 Athena SQL 쿼리를 생성하고 실행합니다.
"""

from typing import List, Optional

from strands import Agent

from .base_agent import BaseMultiAgent
from .constants import ATHENA_OUTPUT_LOCATION


class SQLAgent(BaseMultiAgent):
    """SQL Agent - 쿼리 생성/실행 전문가 (LLM 기반)

    Graph에서 MCP 도구를 사용하여 직접 Athena 쿼리를 생성/실행합니다.
    """

    def __init__(self, model_id: str, tools: Optional[List] = None):
        super().__init__(model_id, tools)

    def _setup_agent(self):
        """SQL Agent 초기화"""
        self.agent = Agent(
            name="sql_agent",
            system_prompt=self.get_system_prompt(),
            model=self.model_id,
            tools=self.tools if self.tools else None
        )

    def get_system_prompt(self) -> str:
        """SQL Agent 시스템 프롬프트 (LLM 기반 SQL 생성)"""
        return f"""
역할: AWS Athena SQL 생성/실행 전문가

입력: Data Expert가 제공한 테이블 정보 + 사용자 요청
출력: 최적화된 SQL 실행 결과

────────────────────────────────────────────
SQL 생성 규칙 (중요!)
────────────────────────────────────────────
**RAG 도메인 지식 우선 원칙:**
- RAG에서 제공한 조건/범위/값은 반드시 그대로 사용
- 일반 상식이나 의학/비즈니스 상식보다 RAG 정보 우선
- 예: RAG가 "정상 범위: N > 8.0"이라면, abnormal은 N <= 8.0

**기본 규칙:**
- SELECT 문만 허용 (DDL/DML 금지)
- 제공된 컬럼명/타입 정확히 사용
- 파티션 키 → WHERE 절에 필터 추가
- 시간 범위 미지정 → 최근 30일 (CURRENT_DATE - INTERVAL '30' DAY)
- 컬럼 별칭(AS)은 영문만 사용 (한글 금지)
- LIMIT 1000 기본 적용

Athena 실행 설정:
- Catalog: AwsDataCatalog
- WorkGroup: primary
- output_location: {ATHENA_OUTPUT_LOCATION}

실행 순서:
1. start_query_execution → QueryExecutionId 획득
2. get_query_execution → 상태 확인 (SUCCEEDED/FAILED 대기)
3. get_query_results (max_results=1000)

필수 출력:
[생성한 SQL]

────────────────────────────────────────────
오류 시 자체 재시도 (최대 2회)
────────────────────────────────────────────
1. 구문 오류 → SQL 수정 후 재실행
2. 스키마 오류 → 컬럼명 확인 후 수정
3. 2회 실패 → 오류 내용을 결과에 포함하여 반환
"""

    def get_tools(self) -> List:
        """SQL Agent 도구 목록"""
        return self.tools
