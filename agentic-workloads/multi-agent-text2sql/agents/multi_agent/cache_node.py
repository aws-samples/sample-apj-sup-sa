"""Cache Node - Semantic Cache 조회 노드

Graph에서 Router 이후에 실행되어, 유사한 질문의 캐시된 응답을 반환합니다.
event_queue를 통해 UI에 캐시 정보를 직접 전달합니다.
"""

import logging
import time
from typing import Any, Optional

from strands.agent.agent_result import AgentResult
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status
from strands.telemetry.metrics import EventLoopMetrics
from strands.types.content import ContentBlock, Message
from strands.types.event_loop import Metrics, Usage

from .semantic_cache import SemanticCache

logger = logging.getLogger(__name__)


class CacheNodeExecutor(MultiAgentBase):
    """Semantic Cache 조회 노드"""

    def __init__(self, threshold: float = 0.90, event_queue=None):
        super().__init__()
        self.threshold = threshold
        self.event_queue = event_queue
        self.id = "cache_node"
        self.name = "cache_node"

    async def invoke_async(
        self,
        task: str | list,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        if invocation_state is None:
            invocation_state = {}

        start_time = time.time()

        # 사용자 원본 질문 가져오기
        router_result = invocation_state.get("router_result")
        user_query = router_result.user_query if router_result else self._extract_text(task)

        # 캐시 조회
        cache = SemanticCache(namespace="response", threshold=self.threshold)
        cached_response = cache.get(user_query)

        if cached_response and not cached_response.get("miss"):
            similarity = cached_response.get("similarity", 0)
            cached_query = cached_response.get("cached_query", "")
            elapsed_ms = cached_response.get("elapsed_ms", 0)
            cached_data = cached_response.get("data", "")

            logger.info(f"[CacheNode] 캐시 히트: '{user_query[:50]}...'")
            print(f"\n⚡ [CacheNode] 캐시 히트! 유사도={similarity:.4f}, query='{user_query[:50]}'")

            # UI에 캐시 정보 전달
            if self.event_queue:
                import re
                sql_match = re.search(r'```sql\s*(.*?)\s*```', cached_data, re.DOTALL)
                sql_text = sql_match.group(1).strip() if sql_match else None

                cache_info = (
                    f"**유사도**: {similarity:.4f}\n\n"
                    f"**캐시된 질문**: {cached_query}\n\n"
                    f"**소요시간**: {elapsed_ms:.0f}ms"
                )
                if sql_text:
                    cache_info += f"\n\n**캐시된 SQL**:\n```sql\n{sql_text}\n```"

                self.event_queue.put({"data": cache_info, "agent": "cache_node"})

            result_text = f"[CACHE_HIT]\n{cached_data}"
        else:
            # 미스 — 유사도 정보가 있으면 표시
            miss_similarity = cached_response.get("similarity", 0) if cached_response else 0
            miss_elapsed = cached_response.get("elapsed_ms", 0) if cached_response else 0

            logger.info(f"[CacheNode] 캐시 미스: '{user_query[:50]}...'")
            print(f"\n⚡ [CacheNode] 캐시 미스. 유사도={miss_similarity:.4f}, query='{user_query[:50]}'")

            if self.event_queue:
                if miss_similarity > 0:
                    cache_info = f"캐시에 유사한 응답이 없습니다.\n\n**최대 유사도**: {miss_similarity:.4f} (임계값: {self.threshold})\n\n**소요시간**: {miss_elapsed:.0f}ms"
                else:
                    cache_info = "캐시가 비어있습니다. 검색을 진행합니다."
                self.event_queue.put({"data": cache_info, "agent": "cache_node"})

            result_text = "[CACHE_MISS]"

        execution_time = round((time.time() - start_time) * 1000)

        agent_result = AgentResult(
            stop_reason="end_turn",
            message=Message(
                role="assistant",
                content=[ContentBlock(text=result_text)],
            ),
            metrics=EventLoopMetrics(),
            state={},
        )

        node_result = NodeResult(
            result=agent_result,
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
        if isinstance(task, str):
            return task
        if isinstance(task, list):
            parts = []
            for block in task:
                if isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
            return " ".join(parts) if parts else str(task)
        return str(task)
