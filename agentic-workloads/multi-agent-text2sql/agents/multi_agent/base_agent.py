"""기본 에이전트 인터페이스

모든 멀티에이전트 시스템의 에이전트가 상속받을 기본 클래스를 정의합니다.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from strands import Agent


class BaseMultiAgent(ABC):
    """멀티에이전트 시스템의 기본 에이전트 클래스"""

    def __init__(self, model_id: str, tools: Optional[List] = None):
        self.model_id = model_id
        self.tools = tools or []
        self.agent: Optional[Agent] = None
        self._setup_agent()

    @abstractmethod
    def _setup_agent(self):
        """에이전트 초기화 - 각 에이전트에서 구현"""
        pass

    @abstractmethod
    def get_system_prompt(self) -> str:
        """에이전트별 시스템 프롬프트 반환"""
        pass

    @abstractmethod
    def get_tools(self) -> list:
        """에이전트별 도구 목록 반환"""
        pass

    def get_agent(self) -> Agent:
        """Swarm에서 사용할 Agent 인스턴스 반환"""
        if not self.agent:
            raise RuntimeError("Agent not initialized")
        return self.agent