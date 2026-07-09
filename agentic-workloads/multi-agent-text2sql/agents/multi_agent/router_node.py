"""Router Node - LLM 기반 요청 분류기

사용자 요청을 LLM을 통해 2-way 분류하여
"데이터 조회" 또는 "일반" 워크플로우 경로를 결정합니다.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from strands import Agent
from strands.agent.agent_result import AgentResult
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status
from strands.telemetry.metrics import EventLoopMetrics
from strands.types.content import ContentBlock, Message
from strands.types.event_loop import Metrics, Usage

logger = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT = """사용자 요청을 분류하세요.
- data_query: 데이터 조회, 분석, 테이블/컬럼 정보, SQL 관련
- general: 인사, 잡담, 도움말 등 데이터와 무관

반드시 data_query 또는 general 중 하나만 출력하세요."""


class RequestType(Enum):
    """요청 유형 분류"""
    DATA_QUERY = "data_query"
    GENERAL = "general"


@dataclass
class RouterResult:
    """Router 분류 결과"""
    request_type: RequestType
    user_query: str
    keywords_matched: list[str] = field(default_factory=list)


class RouterNodeExecutor(MultiAgentBase):
    """LLM 기반 Router를 Graph의 노드 executor로 래핑

    MultiAgentBase를 상속하여 GraphBuilder.add_node()에서
    사용할 수 있도록 합니다.
    """

    def __init__(self, model_id: str, force_data_query: bool = False) -> None:
        super().__init__()
        self.model_id = model_id
        self.force_data_query = force_data_query
        self.agent = Agent(
            name="router",
            system_prompt=ROUTER_SYSTEM_PROMPT,
            model=model_id,
        )
        self.id = "router"
        self.name = "router"

    async def invoke_async(
        self,
        task: str | list,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        """LLM Agent를 호출하여 분류 결과를 MultiAgentResult로 반환"""
        if invocation_state is None:
            invocation_state = {}

        start_time = time.time()

        # task에서 사용자 입력 텍스트 추출
        user_input = self._extract_text(task)

        # eval 모드: LLM 호출 없이 무조건 data_query
        if self.force_data_query:
            request_type = RequestType.DATA_QUERY
        else:
            # LLM Agent 호출
            agent_result = self.agent(user_input)
            response_text = str(agent_result).strip().lower()

            # 응답에서 분류 결과 파싱
            if "data_query" in response_text:
                request_type = RequestType.DATA_QUERY
            else:
                request_type = RequestType.GENERAL

        result = RouterResult(
            request_type=request_type,
            user_query=user_input,
        )

        logger.debug("Router LLM classified: %s", request_type.value)

        # invocation_state에 분류 결과 저장 (조건부 엣지에서 참조)
        invocation_state["router_result"] = result

        # AnalysisContext가 있으면 request_type 반영
        ctx = invocation_state.get("analysis_context")
        if ctx is not None:
            ctx.request_type = result.request_type.value

        execution_time = round((time.time() - start_time) * 1000)

        # AgentResult 생성 (Graph가 후속 노드에 전달)
        result_text = f"[Router 분류 완료] 요청 유형: {result.request_type.value}"
        router_agent_result = AgentResult(
            stop_reason="end_turn",
            message=Message(
                role="assistant",
                content=[ContentBlock(text=result_text)],
            ),
            metrics=EventLoopMetrics(),
            state={},
        )

        node_result = NodeResult(
            result=router_agent_result,
            execution_time=execution_time,
            status=Status.COMPLETED,
            accumulated_usage=Usage(inputTokens=0, outputTokens=0, totalTokens=0),
            accumulated_metrics=Metrics(latencyMs=execution_time),
            execution_count=1,
        )

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={self.name: node_result},
            accumulated_usage=Usage(inputTokens=0, outputTokens=0, totalTokens=0),
            accumulated_metrics=Metrics(latencyMs=execution_time),
            execution_count=1,
            execution_time=execution_time,
        )

    def _extract_text(self, task: Any) -> str:
        """task 입력에서 텍스트를 추출"""
        if isinstance(task, str):
            return task
        if isinstance(task, list):
            parts = []
            for block in task:
                if isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
            return " ".join(parts) if parts else str(task)
        return str(task)
