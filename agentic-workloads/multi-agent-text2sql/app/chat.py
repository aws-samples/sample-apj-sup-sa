"""Chat handling logic for Streamlit.

This module manages chat interactions, user input processing,
and streaming response handling with the agent backend.
"""

from typing import Dict, Any
import streamlit as st

from .session import SessionManager
from .ui import PlaceholderManager, ErrorHandler


class ChatHandler:
    """Handle chat interactions and streaming."""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        self.placeholder_manager = PlaceholderManager()
        self.error_handler = ErrorHandler()

    def handle_user_input(self, prompt: str) -> None:
        """Process user input and generate assistant response."""
        # Add user message to session
        self.session_manager.add_message("user", prompt)

        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)

        # Handle assistant response
        with st.chat_message("assistant"):
            self._handle_assistant_response(prompt)

    def _handle_assistant_response(self, prompt: str) -> None:
        """Handle the assistant response generation and streaming."""
        # Create placeholders
        message_container = st.container()
        status_ph, tool_ph, chain_ph, response_ph, intermediate_container = (
            self.placeholder_manager.create_chat_placeholders(message_container)
        )

        # Setup UI state
        agent = self.session_manager.agent
        ui_state = self.session_manager.get_agent_ui_state()
        if ui_state:
            ui_state.reset()
            ui_state.message_container = message_container

            # Setup handler placeholders
            self.placeholder_manager.setup_ui_handler_placeholders(
                agent, status_ph, tool_ph, chain_ph, response_ph
            )

        try:
            # Stream the response
            self._stream_response(prompt, agent, status_ph, chain_ph, response_ph, intermediate_container)
        except Exception as error:
            # Handle streaming error
            assistant_message = self.error_handler.handle_streaming_error(
                error, status_ph, response_ph, chain_ph
            )
            self.session_manager.add_message("assistant", assistant_message)

    def _is_stream_complete(self, event: Dict[str, Any]) -> bool:
        """Check if event signals stream completion.
        
        Supports both standard format (type: complete/force_stop) and
        legacy format (result/force_stop fields).
        """
        # Standard format
        if event.get("type") in ("complete", "force_stop"):
            return True
        
        # Legacy format (backward compatibility)
        if event.get("result") or event.get("force_stop"):
            return True
        
        return False

    INTERMEDIATE_AGENTS = {"rag_node", "data_expert", "sql_node", "cache_node"}
    AGENT_LABELS = {"rag_node": "📚 문서 검색", "data_expert": "🗂️ 카탈로그 검색", "sql_node": "🔍 SQL 생성 및 실행", "cache_node": "⚡ 캐시 확인"}
    AGENT_PROGRESS = {"rag_node": "🔍 검색 중...", "data_expert": "🔍 검색 중...", "sql_node": "⚙️ 생성 중...", "cache_node": "⚡ 확인 중..."}
    AGENT_DONE = {"rag_node": "✅ 검색 완료", "data_expert": "✅ 검색 완료", "sql_node": "", "cache_node": ""}

    def _stream_response(self, prompt: str, agent, status_ph, chain_ph, response_ph, intermediate_container) -> None:
        """Stream the agent response and handle events."""
        import time

        current_agent = None
        intermediate_expanders = {}
        intermediate_outputs = []
        cache_node_text = ""  # cache_node의 data 이벤트 누적

        sql_generation_start = None
        sql_generation_time = None
        sql_execution_start = None
        sql_execution_time = None

        stream = agent.stream_response(prompt)
        for event in stream:
            event_agent = event.get("agent")

            if event_agent and event_agent != current_agent:
                # 이전 에이전트 완료 — expander 아래에 한 줄 표시
                if current_agent and current_agent in intermediate_expanders:
                    entry = intermediate_expanders[current_agent]
                    elapsed = (time.time() - entry["start_time"]) * 1000
                    entry["elapsed_ms"] = elapsed
                    if current_agent == "sql_node" and sql_generation_time is not None:
                        exec_time = sql_execution_time if sql_execution_time is not None else elapsed - sql_generation_time
                        exec_ph = entry.get("exec_status_placeholder")
                        if exec_ph:
                            exec_ph.markdown(f"✅ 실행 완료 {exec_time:,.0f}ms")
                        else:
                            entry["status_placeholder"].markdown(f"✅ 생성 완료 {sql_generation_time:,.0f}ms / ✅ 실행 완료 {exec_time:,.0f}ms")
                    elif current_agent == "cache_node":
                        # 다음 에이전트가 response_node면 히트, rag_node면 미스
                        if event_agent == "response_node":
                            cache_done = f"✅ 캐시 적중 {elapsed:,.0f}ms"
                        else:
                            cache_done = f"⬜ 캐시 미스 {elapsed:,.0f}ms"
                        entry["status_placeholder"].markdown(cache_done)
                        entry["cache_done_text"] = cache_done
                    else:
                        done_label = self.AGENT_DONE.get(current_agent, "✅ 완료")
                        time_text = f"{done_label} {elapsed:,.0f}ms"
                        entry["status_placeholder"].markdown(time_text)

                current_agent = event_agent

                # 캐시 히트: cache_node가 실행되었고, response_node로 전환 시 캐시 정보 표시
                if event_agent == "response_node" and "cache_node" in intermediate_expanders and "rag_node" not in intermediate_expanders:
                    expander = intermediate_container.expander("⚡ 캐시 확인", expanded=False)
                    expander.markdown("유사한 질문에 대한 이전 응답이 캐시에서 발견되었습니다.\n\n자세한 정보는 터미널 로그의 `[CacheNode]`를 확인하세요.")
                    intermediate_container.markdown("✅ 캐시 적중")
                    intermediate_outputs.append({
                        "label": "⚡ 캐시 확인",
                        "text": "유사한 질문에 대한 이전 응답이 캐시에서 발견되었습니다.",
                        "done_text": "✅ 캐시 적중",
                    })

                if current_agent == "sql_node":
                    sql_generation_start = time.time()

            try:
                if current_agent in self.INTERMEDIATE_AGENTS and "data" in event:
                    text_chunk = event.get("data", "")
                    if text_chunk:
                        if current_agent not in intermediate_expanders:
                            label = self.AGENT_LABELS.get(current_agent, current_agent)
                            expander = intermediate_container.expander(label, expanded=False)
                            placeholder = expander.empty()
                            # expander 아래에 진행 상태 placeholder
                            status_placeholder = intermediate_container.empty()
                            progress_label = self.AGENT_PROGRESS.get(current_agent, "⏳ 진행 중...")
                            status_placeholder.markdown(progress_label)
                            intermediate_expanders[current_agent] = {
                                "label": label,
                                "text": "",
                                "placeholder": placeholder,
                                "status_placeholder": status_placeholder,
                                "start_time": time.time(),
                            }
                        entry = intermediate_expanders[current_agent]
                        entry["text"] += text_chunk
                        entry["placeholder"].markdown(entry["text"])
                elif current_agent in self.INTERMEDIATE_AGENTS:
                    # SQL Agent의 tool_use → 생성 완료, 실행 시작
                    if current_agent == "sql_node" and "current_tool_use" in event:
                        if sql_generation_start and sql_generation_time is None:
                            sql_generation_time = (time.time() - sql_generation_start) * 1000
                            sql_execution_start = time.time()
                            if current_agent in intermediate_expanders:
                                entry = intermediate_expanders[current_agent]
                                entry["status_placeholder"].markdown(f"✅ 생성 완료 {sql_generation_time:,.0f}ms")
                                # 실행 중 placeholder 새로 생성
                                entry["exec_status_placeholder"] = intermediate_container.empty()
                                entry["exec_status_placeholder"].markdown("⚙️ 실행 중...")
                    # SQL Agent의 tool_result → 실행 완료
                    if current_agent == "sql_node" and "tool_result" in event:
                        if sql_execution_start:
                            sql_execution_time = (time.time() - sql_execution_start) * 1000
                            sql_execution_start = time.time()
                else:
                    # response_node or agent-less events → existing pipeline
                    results = agent.event_registry.process_event(event)
                    self.error_handler.handle_handler_errors(results, status_ph)

            except Exception as handler_error:
                self.error_handler.display_handler_error(handler_error, status_ph)

            if self._is_stream_complete(event):
                # 마지막 에이전트 완료 표시
                if current_agent and current_agent in intermediate_expanders:
                    entry = intermediate_expanders[current_agent]
                    if "elapsed_ms" not in entry:
                        elapsed = (time.time() - entry["start_time"]) * 1000
                        entry["elapsed_ms"] = elapsed
                        if current_agent == "sql_node" and sql_generation_time is not None:
                            exec_time = sql_execution_time if sql_execution_time is not None else elapsed - sql_generation_time
                            exec_ph = entry.get("exec_status_placeholder")
                            if exec_ph:
                                exec_ph.markdown(f"✅ 실행 완료 {exec_time:,.0f}ms")
                            else:
                                entry["status_placeholder"].markdown(f"✅ 생성 완료 {sql_generation_time:,.0f}ms / ✅ 실행 완료 {exec_time:,.0f}ms")
                        else:
                            done_label = self.AGENT_DONE.get(current_agent, "✅ 완료")
                            time_text = f"{done_label} {elapsed:,.0f}ms"
                            entry["status_placeholder"].markdown(time_text)
                break

        # Build intermediate_outputs for session persistence
        for agent_name in ("cache_node", "rag_node", "data_expert", "sql_node"):
            if agent_name in intermediate_expanders:
                entry = intermediate_expanders[agent_name]
                elapsed = entry.get("elapsed_ms", 0)
                if agent_name == "sql_node" and sql_generation_time is not None:
                    exec_time = sql_execution_time if sql_execution_time is not None else elapsed - sql_generation_time
                    done_text = f"✅ 생성 완료 {sql_generation_time:,.0f}ms\n\n✅ 실행 완료 {exec_time:,.0f}ms"
                elif agent_name == "cache_node":
                    done_text = entry.get("cache_done_text", f"✅ 캐시 확인 {elapsed:,.0f}ms")
                else:
                    done_label = self.AGENT_DONE.get(agent_name, "✅ 완료")
                    done_text = f"{done_label} {elapsed:,.0f}ms"
                intermediate_outputs.append({
                    "label": entry["label"],
                    "text": entry["text"],
                    "done_text": done_text,
                })

        # Finalize and persist the response
        self._finalize_response(agent, intermediate_outputs)

    def _finalize_response(self, agent, intermediate_outputs=None) -> None:
        """Finalize the assistant response and add to session."""
        for handler in agent.event_registry._handlers:
            if hasattr(handler, "finalize_response"):
                assistant_message = handler.finalize_response()
                if intermediate_outputs:
                    assistant_message["intermediate_outputs"] = intermediate_outputs
                self.session_manager.add_message("assistant", assistant_message)
                break