"""Data Expert Agent 단위 테스트 (LLM 기반)

Requirements:
- 2.1: MCP 도구를 사용하여 AWS Athena 카탈로그 조회
- 2.3: Strands Agent의 LLM을 통해 테이블 스키마 분석 및 적합한 테이블 추천
"""

import pytest
from agents.multi_agent.data_expert_agent import DataExpertAgent


class TestSystemPrompt:
    """시스템 프롬프트 테스트"""

    @pytest.fixture
    def agent(self):
        return DataExpertAgent(model_id="test-model")

    def test_system_prompt_includes_catalog_workflow(self, agent):
        """시스템 프롬프트에 카탈로그 탐색 워크플로우 포함"""
        prompt = agent.get_system_prompt()

        assert "list_databases" in prompt
        assert "list_tables" in prompt
        assert "get_table" in prompt

    def test_system_prompt_includes_output_format(self, agent):
        """시스템 프롬프트에 출력 형식 규칙 포함"""
        prompt = agent.get_system_prompt()

        assert "결과 출력 형식" in prompt
        assert "컬럼 매핑" in prompt

    def test_system_prompt_includes_routing_signal(self, agent):
        """시스템 프롬프트에 라우팅 시그널 포함"""
        prompt = agent.get_system_prompt()

        assert "[SQL_NEEDED]" in prompt
        assert "[INFO_ONLY]" in prompt


class TestAgentInitialization:
    """에이전트 초기화 테스트"""

    def test_agent_created_with_correct_name(self):
        agent = DataExpertAgent(model_id="test-model")
        assert agent.agent.name == "data_expert"

    def test_agent_stores_tools(self):
        """tools 파라미터가 저장되는지 확인"""
        agent = DataExpertAgent(model_id="test-model", tools=[])
        assert agent.get_tools() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
