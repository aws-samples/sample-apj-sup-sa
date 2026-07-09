"""Response Node - 최종 응답 포맷팅 에이전트

Graph 패턴에서 최종 노드로 동작하며, 이전 노드들의 결과를
사용자 친화적인 형식으로 포맷팅하여 응답합니다.
"""

from typing import List, Optional

from strands import Agent

from .base_agent import BaseMultiAgent


class ResponseNode(BaseMultiAgent):
    """Response Node - 최종 응답 포맷팅 에이전트

    데이터 분석 결과, 정보 조회 결과, 에러 결과를
    사용자에게 보여줄 형식으로 정리합니다.
    """

    def __init__(self, model_id: str, tools: Optional[List] = None):
        super().__init__(model_id, tools)

    def _setup_agent(self):
        """Response Node 초기화"""
        self.agent = Agent(
            name="response_node",
            system_prompt=self.get_system_prompt(),
            model=self.model_id,
            tools=self.tools if self.tools else None,
        )

    def get_system_prompt(self) -> str:
        """Response Node 시스템 프롬프트"""
        return """
역할: 최종 응답 포맷팅 전문가

이전 에이전트들의 결과를 받아 사용자에게 보여줄 최종 응답을 생성합니다.

────────────────────────────────────────────
데이터 분석 결과 포맷팅
────────────────────────────────────────────

SQL 실행 결과가 포함된 경우:
1. 실행된 SQL 쿼리를 코드 블록으로 표시
2. 결과 데이터를 표 형식으로 정리
3. 핵심 인사이트 요약 (3줄 이내)
4. 추가 분석 가능한 방향 제안

────────────────────────────────────────────
정보 조회 결과 포맷팅
────────────────────────────────────────────

테이블/컬럼 정보가 포함된 경우:
1. 발견된 테이블 목록을 구조적으로 정리
2. 각 테이블의 주요 컬럼과 타입 표시
3. 파티션 키 정보 표시
4. 가능한 분석 예시 제안

────────────────────────────────────────────
에러 결과 포맷팅
────────────────────────────────────────────

오류가 발생한 경우:
1. 무엇이 실패했는지 명확히 설명
2. 가능한 원인 제시
3. 사용자가 취할 수 있는 조치 안내

────────────────────────────────────────────
일반 규칙
────────────────────────────────────────────
- 한국어로 응답
- 마크다운 형식 사용
- 간결하고 구조적으로 정리
- 기술 용어는 필요한 경우에만 사용
"""

    def get_tools(self) -> List:
        """Response Node 도구 목록"""
        return self.tools
