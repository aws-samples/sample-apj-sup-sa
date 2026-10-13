"""Multi-Agent Text2SQL System

Strands Graph 패턴을 사용하여 결정론적 워크플로우로 에이전트가 협업하는 멀티에이전트 시스템입니다.
기존 MyCustomAgent와 동일한 인터페이스를 제공하여 호환성을 유지합니다.
"""

import logging
import queue
import sys
import threading
import time
import os
from typing import Any, Callable, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

from strands.multiagent import GraphBuilder
from strands.tools.mcp.mcp_client import MCPClient
from mcp import stdio_client, StdioServerParameters

from agents.events.registry import EventRegistry
from agents.events.lifecycle import (
    DebugHandler,
    LifecycleHandler,
    LoggingHandler,
    ReasoningHandler,
)
from agents.events.ui import StreamlitUIState

from .data_expert_agent import DataExpertAgent
from .sql_agent import SQLAgent
from .rag_agent import RAGAgent
from .router_node import RouterNodeExecutor
from .response_node import ResponseNode
# from .cache_node import CacheNodeExecutor  # <- 주석을 해제하세요.
from .shared_context import AnalysisContext, GraphConfig
from .graph_conditions import (
    is_data_query,
    is_general_query,
    # is_cache_hit,    # <- 주석을 해제하세요.
    # is_cache_miss,   # <- 주석을 해제하세요.
    rag_completed,
    needs_sql,
    no_sql_needed,
    sql_succeeded,
    needs_more_tables,
    sql_max_retries,
)
from .event_adapter import (
    SwarmEventAdapter,
    StreamlitSwarmUIHandler,
)


class MultiAgentText2SQL:
    """멀티에이전트 Text2SQL 시스템

    기존 MyCustomAgent와 동일한 인터페이스를 제공하면서
    내부적으로는 Graph 패턴을 사용한 결정론적 멀티에이전트 협업을 수행합니다.
    """

    def __init__(self, model_id: str, force_data_query: bool = False):
        self.model_id = model_id
        self._force_data_query = force_data_query
        self.event_queue = queue.Queue()
        self.event_registry = EventRegistry()
        self.ui_state = StreamlitUIState()
        self._logged_tool_ids: set = set()

        self._debug_enabled = False
        self._debug_handler: Optional[DebugHandler] = None

        self._setup_handlers()

        self.mcp_client = self._setup_mcp_client()

        # Graph 및 에이전트들 초기화
        self.graph = self._create_graph()

        self.analysis_context = AnalysisContext()

        self._event_adapter = SwarmEventAdapter(
            event_queue=self.event_queue,
            event_registry=self.event_registry,
        )

        self._swarm_ui_handler = StreamlitSwarmUIHandler(self._event_adapter, self.ui_state)

        self._current_agent: str = "router"
    
    def _setup_handlers(self):
        """핵심 핸들러들을 등록합니다. (Requirements 5.3)
        
        기존 이벤트 시스템과 호환되는 핸들러들을 설정합니다.
        """
        self.event_registry.register(LifecycleHandler())
        self.event_registry.register(ReasoningHandler())
        self.event_registry.register(LoggingHandler(log_level="INFO"))
        
        # 디버그 핸들러 참조 저장 (Requirements 5.4)
        self._debug_handler = DebugHandler(debug_enabled=self._debug_enabled)
        self.event_registry.register(self._debug_handler)
    
    def _setup_mcp_client(self) -> MCPClient:
        """MCP 클라이언트 설정 (Requirements 5.5)
        
        AWS Athena 데이터 처리를 위한 MCP 서버 연결을 설정합니다.
        MCP 클라이언트는 중앙에서 관리되며 모든 에이전트가 공유합니다.
        """
        # MCP 서버 환경 변수 설정 (현재 프로세스의 AWS credential 관련 변수를 상속)
        mcp_env = {
            "FASTMCP_LOG_LEVEL": "ERROR",
            "LOGURU_LEVEL": "ERROR",
            "LOG_LEVEL": "ERROR",
            "AWS_DEFAULT_REGION": os.environ.get("AWS_DEFAULT_REGION", "us-west-2"),
        }

        for key in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                     "AWS_SESSION_TOKEN", "ATHENA_OUTPUT_LOCATION"):
            val = os.environ.get(key)
            if val:
                mcp_env[key] = val
        
        mcp_client = MCPClient(
            lambda: stdio_client(
                StdioServerParameters(
                    command="uv",
                    args=["run", "awslabs.aws-dataprocessing-mcp-server"],
                    env=mcp_env,
                ),
            ),
            tool_filters={"allowed": [
                "manage_aws_athena_query_executions",
                "manage_aws_athena_data_catalogs",
                "manage_aws_athena_databases_and_tables",
                "manage_aws_athena_workgroups"
            ]}
        )
        mcp_client.start()
        return mcp_client
    
    def get_mcp_client(self) -> Optional[MCPClient]:
        """MCP 클라이언트 접근 (Requirements 5.5)
        
        AWS 데이터 처리 도구에 대한 접근을 관리합니다.
        
        Returns:
            MCPClient 인스턴스 또는 None
        """
        return self.mcp_client
    
    def is_mcp_client_active(self) -> bool:
        """MCP 클라이언트 활성 상태 확인 (Requirements 5.5)
        
        Returns:
            MCP 클라이언트가 활성 상태인지 여부
        """
        return self.mcp_client is not None
    
    def _get_mcp_tools(self) -> List:
        """MCP 클라이언트에서 도구 목록 가져오기
        
        Returns:
            MCP 도구 목록
        """
        if self.mcp_client:
            try:
                return self.mcp_client.list_tools_sync()
            except Exception as e:
                logger.warning("Failed to list MCP tools: %s", e)
                return []
        return []
    
    def _filter_tools_by_name(self, tools: List, allowed_names: List[str]) -> List:
        """도구 목록에서 허용된 이름의 도구만 필터링
        
        Args:
            tools: 전체 도구 목록
            allowed_names: 허용할 도구 이름 목록
            
        Returns:
            필터링된 도구 목록
        """
        if not tools:
            return []
        
        filtered = []
        for tool in tools:
            # MCPAgentTool은 tool_name 속성을 사용
            tool_name = getattr(tool, 'tool_name', None)
            # 일반 도구는 name 속성 사용
            if tool_name is None:
                tool_name = getattr(tool, 'name', None)
            # dict인 경우
            if tool_name is None and isinstance(tool, dict):
                tool_name = tool.get('name')
            
            if tool_name and tool_name in allowed_names:
                filtered.append(tool)
        
        return filtered

    def _create_graph(self):
        """Graph 및 에이전트들 생성

        GraphBuilder를 사용하여 결정론적 워크플로우를 구성합니다.
        Router → RAG → DataExpert → SQL → Response
        """
        mcp_tools = self._get_mcp_tools()

        logger.info("[MCP Tools] %d개 도구 로드됨", len(mcp_tools))
        for tool in mcp_tools:
            tool_name = getattr(tool, 'name', None) or (tool.get('name') if isinstance(tool, dict) else str(tool))
            logger.info("   - %s", tool_name)

        data_expert_tools = self._filter_tools_by_name(
            mcp_tools,
            ["manage_aws_athena_data_catalogs", "manage_aws_athena_databases_and_tables"],
        )
        sql_agent_tools = self._filter_tools_by_name(
            mcp_tools,
            ["manage_aws_athena_query_executions", "manage_aws_athena_workgroups"],
        )

        # 에이전트 생성
        self.router_executor = RouterNodeExecutor(self.model_id, force_data_query=self._force_data_query)
        # 캐시 노드 생성
        # self.cache_node = CacheNodeExecutor(threshold=0.90, event_queue=self.event_queue)
        self.data_expert = DataExpertAgent(self.model_id, tools=data_expert_tools)
        self.sql_agent = SQLAgent(self.model_id, tools=sql_agent_tools)
        self.response_node = ResponseNode(self.model_id, tools=[])

        opensearch_endpoint = os.environ.get("OPENSEARCH_ENDPOINT")
        opensearch_index = os.environ.get("OPENSEARCH_INDEX", "schema_docs")
        opensearch_username = os.environ.get("OPENSEARCH_USERNAME")
        opensearch_password = os.environ.get("OPENSEARCH_PASSWORD")

        self.rag_agent = RAGAgent(
            model_id=self.model_id,
            opensearch_endpoint=opensearch_endpoint,
            opensearch_index=opensearch_index,
            opensearch_username=opensearch_username,
            opensearch_password=opensearch_password,
            tools=[],
        )

        # callback handler 설정
        self.router_executor.agent.callback_handler = self._create_callback_handler("router")
        self.data_expert.agent.callback_handler = self._create_callback_handler("data_expert")
        self.sql_agent.agent.callback_handler = self._create_callback_handler("sql_node")
        self.rag_agent.agent.callback_handler = self._create_callback_handler("rag_node")
        self.response_node.agent.callback_handler = self._create_callback_handler("response_node")

        # invocation_state 설정
        self._invocation_state = {
            "mcp_client": self.mcp_client,
            "aws_config": {
                "region": os.environ.get("AWS_DEFAULT_REGION", "us-west-2"),
                "profile": os.environ.get("AWS_PROFILE", "default"),
            },
            "debug_mode": False,
            "session_id": f"session_{int(time.time())}",
            "analysis_context": None,
            "rag_enabled": self.rag_agent.is_rag_enabled(),
            "opensearch_endpoint": opensearch_endpoint,
            "opensearch_index": opensearch_index,
        }

        # Graph 구성
        config = GraphConfig()
        builder = GraphBuilder()

        # Node 등록
        builder.add_node(self.router_executor, "router")
        # Graph에 Cache Node 등록
        # builder.add_node(self.cache_node, "cache_node")
        builder.add_node(self.rag_agent.agent, "rag_node")
        builder.add_node(self.data_expert.agent, "data_expert")
        builder.add_node(self.sql_agent.agent, "sql_node")
        builder.add_node(self.response_node.agent, "response_node")

        # 엣지 정의
        # Cache Node를 거치도록 엣지가 정의되어 있습니다.
        # builder.add_edge("router", "cache_node", condition=is_data_query)
        # builder.add_edge("cache_node", "response_node", condition=is_cache_hit)
        # builder.add_edge("cache_node", "rag_node", condition=is_cache_miss)
        builder.add_edge("router", "rag_node", condition=is_data_query)
        builder.add_edge("router", "response_node", condition=is_general_query)
        # RAG 완료 후 Data Expert (RAG 결과를 활용하여 탐색 범위 축소)
        builder.add_edge("rag_node", "data_expert", condition=rag_completed)
        # Data Expert 완료 후 SQL 또는 Response
        builder.add_edge("data_expert", "sql_node", condition=needs_sql)
        builder.add_edge("data_expert", "response_node", condition=no_sql_needed)
        # SQL 분기
        builder.add_edge("sql_node", "response_node", condition=sql_succeeded)
        builder.add_edge("sql_node", "data_expert", condition=needs_more_tables)
        builder.add_edge("sql_node", "response_node", condition=sql_max_retries)

        builder.set_entry_point("router")
        builder.set_max_node_executions(config.max_node_executions)
        builder.set_execution_timeout(config.execution_timeout)
        builder.set_node_timeout(config.node_timeout)
        builder.reset_on_revisit(config.reset_on_revisit)

        return builder.build()
    
    def _create_callback_handler(self, agent_name: str):
        """에이전트별 callback handler 생성

        Swarm 패턴과 동일하게 callback에서 직접 이벤트 큐에 넣습니다.
        """
        def handler(**kwargs):
            self._log_agent_event_to_terminal(kwargs, agent_name)

            # router의 이벤트는 UI에 표시하지 않음
            if agent_name == "router":
                return

            if "data" in kwargs:
                text = kwargs.get("data", "")
                if text:
                    self.event_queue.put({"data": text, "agent": agent_name})
            elif "current_tool_use" in kwargs:
                self.event_queue.put({"current_tool_use": kwargs["current_tool_use"], "agent": agent_name})
            elif "tool_result" in kwargs:
                self.event_queue.put({"tool_result": kwargs["tool_result"], "agent": agent_name})
            elif "reasoningText" in kwargs:
                self.event_queue.put({"reasoningText": kwargs["reasoningText"], "agent": agent_name})

        return handler
    
    def _log_agent_event_to_terminal(self, event: Dict[str, Any], agent_name: str = "") -> None:
        """에이전트 간 대화 이벤트를 터미널에 로깅합니다.
        
        UI에는 표시하지 않고 터미널에서만 에이전트 간 대화를 확인할 수 있습니다.
        """
        # 이벤트 타입 추출
        event_type = event.get("type", "")
        
        # 에이전트 상태 이벤트 (node_start, node_stop, handoff)
        if "multiagent_node_start" in str(event) or event_type == "multiagent_node_start":
            node_id = event.get("node_id", "unknown")
            print(f"\n🚀 [Agent Start] {node_id}", file=sys.stderr)
        
        elif "multiagent_node_stop" in str(event) or event_type == "multiagent_node_stop":
            node_id = event.get("node_id", "unknown")
            print(f"\n✅ [Agent Stop] {node_id}", file=sys.stderr)
        
        elif "multiagent_handoff" in str(event) or event_type == "multiagent_handoff":
            from_agents = event.get("from_node_ids", [])
            to_agents = event.get("to_node_ids", [])
            from_str = from_agents[0] if from_agents else "unknown"
            to_str = to_agents[0] if to_agents else "unknown"
            print(f"\n🔀 [Handoff] {from_str} → {to_str}", file=sys.stderr)
        
        # # 텍스트 스트리밍 이벤트
        # elif "data" in event:
        #     text = event.get("data", "")
        #     if text:
        #         # 줄바꿈 없이 스트리밍 출력
        #         print(text, end="", flush=True, file=sys.stderr)
        
        # 도구 사용 이벤트 (새 도구 호출 시작 시에만 로깅)
        elif "current_tool_use" in event:
            tool_info = event.get("current_tool_use", {})
            tool_use_id = tool_info.get("toolUseId", "")
            tool_name = tool_info.get("name", "")
            # 도구 이름이 있고, 새로운 도구 호출인 경우에만 로깅
            if tool_name and tool_use_id:
                if tool_use_id not in self._logged_tool_ids:
                    self._logged_tool_ids.add(tool_use_id)
                    print(f"\n🔧 [Tool Call] {tool_name}", file=sys.stderr)
        
        # 도구 결과 이벤트
        elif "tool_result" in event:
            tool_result = event.get("tool_result", {})
            status = tool_result.get("status", "unknown")
            print(f"\n📋 [Tool Result] status={status}", file=sys.stderr)
        
        # # 추론 이벤트
        # elif "reasoningText" in event:
        #     reasoning = event.get("reasoningText", "")
        #     if reasoning:
        #         print(f"\n💭 [Reasoning] {reasoning[:100]}...", file=sys.stderr)
        
        # 완료 이벤트
        elif event_type == "complete" or "complete" in event:
            print(f"\n🏁 [Complete]", file=sys.stderr)
            self._logged_tool_ids.clear()
    
    def set_callback_handler(self, callback: Callable) -> None:
        """외부 콜백 핸들러 설정 (인터페이스 호환용 no-op)"""
        pass

    def remove_callback_handler(self) -> None:
        """외부 콜백 핸들러 제거 (인터페이스 호환용 no-op)"""
        pass
    
    def stream_response(self, user_input: str) -> Generator[Dict[str, Any], None, None]:
        """사용자 입력에 대한 스트리밍 응답을 생성합니다.

        백그라운드 스레드에서 Graph를 동기 실행하고, callback handler가
        이벤트 큐를 통해 Streamlit에 토큰을 전달합니다.
        """
        self.ui_state.reset()
        self._current_agent = "router"

        # Router는 매 요청을 독립적으로 분류해야 하므로 대화 히스토리 초기화
        self.router_executor.agent.messages.clear()

        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break

        self.analysis_context = AnalysisContext(user_query=user_input)

        # 요청마다 invocation_state를 shallow copy하여 스레드 간 공유 방지
        request_state = dict(self._invocation_state)
        request_state["analysis_context"] = self.analysis_context
        request_state.pop("router_result", None)

        graph_result = None
        graph_error = None
        graph_complete = threading.Event()

        def run_graph():
            """백그라운드에서 Graph 동기 실행 (callback_handler가 이벤트 큐에 직접 전달)"""
            nonlocal graph_result, graph_error
            try:
                graph_result = self.graph(
                    user_input,
                    invocation_state=request_state,
                )
            except Exception as e:
                graph_error = str(e)
            finally:
                self.event_queue.put({"type": "_graph_complete"})
                graph_complete.set()

        thread = threading.Thread(target=run_graph)
        thread.start()

        yield {"type": "start"}

        while not graph_complete.is_set() or not self.event_queue.empty():
            try:
                event = self.event_queue.get(timeout=0.1)
                if event.get("type") == "_graph_complete":
                    continue
                yield event
            except queue.Empty:
                continue

        thread.join(timeout=10)

        # 큐에 남은 이벤트 drain
        while not self.event_queue.empty():
            try:
                event = self.event_queue.get_nowait()
                if event.get("type") != "_graph_complete":
                    yield event
            except queue.Empty:
                break

        if thread.is_alive():
            logger.warning("Graph execution thread did not terminate within timeout")
            yield {"type": "force_stop", "force_stop_reason": "Graph execution thread timeout"}
            return

        if graph_error:
            yield {"type": "force_stop", "force_stop_reason": graph_error}
            return

        # 캐시 미스였으면 최종 응답을 캐시에 저장
        # cache_node가 실행된 경우에만 캐시 저장 시도
        if graph_result and graph_result.results.get("cache_node"):
            cache_text = str(graph_result.results["cache_node"].result)
            is_hit = "[CACHE_HIT]" in cache_text

            if not is_hit:
                try:
                    from .semantic_cache import SemanticCache
                    # response_node의 최종 응답 텍스트만 추출
                    response_result = graph_result.results.get("response_node")
                    if response_result and response_result.result:
                        msg = response_result.result.message
                        # content에서 텍스트 추출 (Message 객체 또는 dict 대응)
                        content = msg.content if hasattr(msg, 'content') else msg.get('content', [])
                        parts = []
                        for block in content:
                            if isinstance(block, dict):
                                parts.append(block.get("text", ""))
                            elif hasattr(block, 'text'):
                                parts.append(block.text)
                            else:
                                parts.append(str(block))
                        result_text = "".join(parts)
                        if result_text:
                            cache = SemanticCache(namespace="response", threshold=0.90)
                            cache.set(self.analysis_context.user_query, result_text)
                except Exception as e:
                    logger.warning(f"[CacheStore] 캐시 저장 실패: {e}")

        yield {"type": "complete", "result": graph_result}
    
    def get_ui_state(self) -> StreamlitUIState:
        """현재 UI 상태를 반환합니다 (Requirements 5.2)
        
        이 메서드는 Streamlit 프론트엔드에서 필수로 요구됩니다.
        기존 MyCustomAgent와 동일한 인터페이스를 제공합니다.
        
        Returns:
            StreamlitUIState 인스턴스
        """
        return self.ui_state
    
    def enable_debug_mode(self, enabled: bool = True):
        """디버그 모드를 토글합니다. (Requirements 5.4)
        
        모든 에이전트의 디버그 정보를 통합하여 표시합니다.
        
        Args:
            enabled: 디버그 모드 활성화 여부
        """
        self._debug_enabled = enabled
        
        # 디버그 핸들러 업데이트
        if self._debug_handler:
            self._debug_handler.debug_enabled = enabled
        
        # 이벤트 레지스트리의 모든 디버그 핸들러 업데이트
        for handler in self.event_registry._handlers:
            if isinstance(handler, DebugHandler):
                handler.debug_enabled = enabled
        
        # invocation_state에도 반영 (Requirements 4.3)
        self._invocation_state["debug_mode"] = enabled
    
    def is_debug_enabled(self) -> bool:
        """디버그 모드 활성화 상태 확인 (Requirements 5.4)
        
        Returns:
            디버그 모드 활성화 여부
        """
        return self._debug_enabled
    
    def get_debug_info(self) -> Dict[str, Any]:
        """모든 에이전트의 디버그 정보 통합 반환"""
        debug_info = {
            "debug_enabled": self._debug_enabled,
            "event_log": [],
            "agents": {},
            "workflow_status": self.get_workflow_status(),
            "analysis_context": {
                "user_query": self.analysis_context.user_query,
                "business_intent": self.analysis_context.business_intent,
                "tables_count": len(self.analysis_context.identified_tables),
                "has_sql": self.analysis_context.generated_sql is not None,
                "has_results": self.analysis_context.results is not None,
                "error_count": len(self.analysis_context.error_messages),
            },
        }

        if self._debug_handler and self._debug_handler.debug_enabled:
            debug_info["event_log"] = self._debug_handler.event_log.copy()

        if hasattr(self, "data_expert"):
            debug_info["agents"]["data_expert"] = {
                "initialized": self.data_expert.agent is not None
            }

        if hasattr(self, "sql_agent"):
            debug_info["agents"]["sql_agent"] = {
                "initialized": self.sql_agent.agent is not None
            }

        if hasattr(self, "rag_agent"):
            debug_info["agents"]["rag_agent"] = {
                "initialized": self.rag_agent.agent is not None,
                "rag_enabled": self.rag_agent.is_rag_enabled(),
                "status": self.rag_agent.get_status(),
            }

        if hasattr(self, "response_node"):
            debug_info["agents"]["response_node"] = {
                "initialized": self.response_node.agent is not None
            }

        return debug_info
    
    def get_analysis_context(self) -> AnalysisContext:
        """현재 분석 컨텍스트를 반환합니다."""
        return self.analysis_context
    
    def reset_context(self):
        """분석 컨텍스트를 초기화합니다."""
        self.analysis_context = AnalysisContext()

    def get_workflow_status(self) -> Dict[str, Any]:
        """현재 워크플로우 상태를 반환합니다."""
        return self._event_adapter.get_current_status()
    
    def get_event_registry(self) -> EventRegistry:
        """이벤트 레지스트리 반환 (Requirements 5.3)
        
        기존 이벤트 시스템과의 호환성을 위해 이벤트 레지스트리에 접근합니다.
        
        Returns:
            EventRegistry 인스턴스
        """
        return self.event_registry
    
    def register_event_handler(self, handler) -> None:
        """이벤트 핸들러 등록 (Requirements 5.3)
        
        기존 이벤트 시스템과 호환되는 핸들러를 등록합니다.
        
        Args:
            handler: EventHandler 인스턴스
        """
        self.event_registry.register(handler)
    
    def get_event_adapter(self) -> SwarmEventAdapter:
        """이벤트 어댑터 반환 (Requirements 1.5, 5.3)
        
        Swarm 이벤트를 Streamlit 이벤트로 변환하는 어댑터에 접근합니다.
        
        Returns:
            SwarmEventAdapter 인스턴스
        """
        return self._event_adapter
    
    def get_agent_progress(self) -> List[Dict[str, Any]]:
        """에이전트 진행 상황 반환 (Requirements 1.5)
        
        이벤트 어댑터를 통해 에이전트 진행 상황을 반환합니다.
        
        Returns:
            에이전트 진행 상황 목록
        """
        return self._event_adapter.get_agent_progress()
    
    def get_rag_agent(self) -> Optional[RAGAgent]:
        """RAG Agent 인스턴스 반환 (Requirements 3.1)
        
        Returns:
            RAGAgent 인스턴스 또는 None
        """
        return getattr(self, 'rag_agent', None)
    
    def is_rag_enabled(self) -> bool:
        """RAG 활성화 상태 확인 (Requirements 3.5)
        
        Returns:
            RAG가 활성화되어 있는지 여부
        """
        if hasattr(self, 'rag_agent') and self.rag_agent:
            return self.rag_agent.is_rag_enabled()
        return False
    
    def set_status_placeholder(self, placeholder) -> None:
        """상태 표시용 placeholder 설정 (Requirements 1.5)
        
        Streamlit UI에서 에이전트 상태를 표시할 placeholder를 설정합니다.
        
        Args:
            placeholder: Streamlit placeholder 객체
        """
        self._swarm_ui_handler.set_status_placeholder(placeholder)
    
    def close(self) -> None:
        """리소스 정리 - MCP 클라이언트 등 외부 리소스를 명시적으로 해제합니다."""
        if hasattr(self, 'mcp_client') and self.mcp_client:
            try:
                self.mcp_client.stop()
            except Exception:
                pass