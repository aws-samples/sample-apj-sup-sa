"""SQL Agent 단위 테스트 (LLM 기반)

Requirements:
- 3.1: 카탈로그 정보(테이블 스키마, 컬럼, 파티션 키)를 시스템 프롬프트에 포함
- 3.2: Strands Agent의 LLM을 통해 비즈니스 의도를 해석하고 SQL 쿼리 생성
"""

import pytest
from agents.multi_agent.sql_agent import SQLAgent
from agents.multi_agent.constants import (
    POLLING_INTERVAL_SECONDS,
    MAX_POLLING_ATTEMPTS,
    MAX_QUERY_RESULTS,
    DEFAULT_CATALOG,
    DEFAULT_WORKGROUP,
)


class TestSQLAgentConstants:
    """SQL Agent 상수 테스트"""

    def test_polling_interval_is_5_seconds(self):
        """Requirements 3.4: 폴링 간격이 5초인지 확인"""
        assert POLLING_INTERVAL_SECONDS == 5

    def test_max_polling_attempts_is_5(self):
        """Requirements 3.4: 최대 폴링 횟수가 5회인지 확인"""
        assert MAX_POLLING_ATTEMPTS == 5

    def test_max_query_results_is_1000(self):
        """Requirements 3.5: 최대 결과 행 수가 1000인지 확인"""
        assert MAX_QUERY_RESULTS == 1000

    def test_default_catalog(self):
        """기본 카탈로그가 AwsDataCatalog인지 확인"""
        assert DEFAULT_CATALOG == "AwsDataCatalog"

    def test_default_workgroup(self):
        """기본 워크그룹이 primary인지 확인"""
        assert DEFAULT_WORKGROUP == "primary"


class TestSystemPrompt:
    """시스템 프롬프트 테스트"""

    @pytest.fixture
    def agent(self):
        return SQLAgent(model_id="test-model")

    def test_system_prompt_includes_sql_generation_rules(self, agent):
        """시스템 프롬프트에 SQL 생성 규칙 포함"""
        prompt = agent.get_system_prompt()

        assert "SQL 생성 규칙" in prompt

    def test_system_prompt_includes_athena_workflow(self, agent):
        """시스템 프롬프트에 Athena 워크플로우 포함"""
        prompt = agent.get_system_prompt()

        assert "start_query_execution" in prompt
        assert "get_query_results" in prompt

    def test_system_prompt_includes_rag_priority(self, agent):
        """시스템 프롬프트에 RAG 우선 원칙 포함"""
        prompt = agent.get_system_prompt()

        assert "RAG 도메인 지식 우선 원칙" in prompt


class TestAgentInitialization:
    """에이전트 초기화 테스트"""

    def test_agent_created_with_correct_name(self):
        agent = SQLAgent(model_id="test-model")
        assert agent.agent.name == "sql_agent"

    def test_agent_stores_tools(self):
        """tools 파라미터가 저장되는지 확인"""
        agent = SQLAgent(model_id="test-model", tools=[])
        assert agent.get_tools() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
