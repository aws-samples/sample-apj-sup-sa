# 구현 계획: Swarm → Graph 패턴 마이그레이션

## 개요

기존 Strands Swarm 패턴을 Graph 패턴으로 전환합니다. 기존 에이전트 로직은 최대한 재사용하고, 오케스트레이션 레이어만 교체합니다. Python + hypothesis를 사용합니다.

## Tasks

- [-] 1. RouterNode 및 GraphConfig 구현
  - [ ] 1.1 `agents/multi_agent/router_node.py` 생성: `RequestType` enum, `RouterResult` dataclass, `RouterNode` 클래스 구현. 키워드 매칭 기반 `classify(user_input: str) -> RouterResult` 메서드 작성. RouterNode를 Strands Agent로 래핑하여 GraphNode executor로 사용 가능하게 함
    - INFO_KEYWORDS, ANALYSIS_KEYWORDS 리스트 정의
    - 키워드 매칭 로직: 정보 조회 키워드 우선 매칭, 없으면 분석 키워드 확인, 둘 다 없으면 기본값 DATA_ANALYSIS
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_
  - [ ] 1.2 `agents/multi_agent/shared_context.py`에 `GraphConfig` dataclass 추가 (max_node_executions, execution_timeout, node_timeout, reset_on_revisit, max_sql_retries, max_data_expert_loops). `AnalysisContext`에 `request_type`, `sql_retry_count`, `data_expert_loop_count`, `sql_error_type` 필드 추가
    - _Requirements: 2.6, 9.4_
  - [ ]* 1.3 RouterNode 속성 기반 테스트 작성
    - **Property 1: Router 분류 완전성** - 임의 문자열에 대해 classify 결과가 유효한 RequestType
    - **Property 2: Router 키워드 매칭 정확성** - 키워드 포함 문자열에 대해 올바른 분류
    - **Validates: Requirements 1.1, 1.2, 1.3**

- [ ] 2. ResponseNode 구현
  - [ ] 2.1 `agents/multi_agent/response_node.py` 생성: `BaseMultiAgent`를 상속하는 `ResponseNode` 클래스 구현. 이전 노드 결과를 통합하여 사용자 친화적 응답을 생성하는 시스템 프롬프트 작성
    - 데이터 분석 결과: SQL + 결과 데이터 포함
    - 정보 조회 결과: 테이블/컬럼 정보 정리
    - 에러 결과: 실패 원인 + 가능한 조치
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [ ] 3. 기존 에이전트 시스템 프롬프트 수정
  - [ ] 3.1 `agents/multi_agent/data_expert_agent.py`의 시스템 프롬프트에서 `handoff_to_agent` 관련 지시사항 제거. MCP 도구로 카탈로그 탐색 후 결과 반환에 집중하도록 단순화
    - _Requirements: 5.4, 9.3_
  - [ ] 3.2 `agents/multi_agent/sql_agent.py`의 시스템 프롬프트에서 `handoff_to_agent` 관련 지시사항 제거. SQL 생성/실행 후 결과 반환에 집중하도록 단순화. 구문/스키마 오류 시 자체 재시도 지시 유지 (최대 2회)
    - _Requirements: 3.1, 5.4, 9.3_
  - [ ] 3.3 `agents/multi_agent/rag_agent.py`의 시스템 프롬프트에서 `handoff_to_agent` 관련 지시사항 제거. 검색 결과 반환에 집중하도록 단순화
    - _Requirements: 5.4, 9.3_
  - [ ]* 3.4 handoff_to_agent 제거 속성 테스트 작성
    - **Property 3: handoff_to_agent 제거 확인** - 모든 에이전트의 시스템 프롬프트에 "handoff_to_agent" 미포함
    - **Validates: Requirements 5.4, 9.3**

- [ ] 4. Checkpoint - 개별 컴포넌트 검증
  - 모든 테스트 통과 확인, 사용자에게 질문이 있으면 문의

- [ ] 5. 조건부 엣지 함수 및 Graph 구성
  - [ ] 5.1 `agents/multi_agent/graph_conditions.py` 생성: `is_data_analysis(state)`, `is_info_query(state)`, `sql_succeeded(state)`, `needs_more_tables(state)`, `sql_max_retries(state)` 조건 함수 구현. 각 함수는 `GraphState`와 `invocation_state`를 참조하여 분기 결정
    - _Requirements: 2.5, 3.1, 3.2, 3.3, 3.4_
  - [ ] 5.2 `agents/multi_agent/multi_agent_text2sql.py`를 Graph 패턴으로 전면 리팩토링:
    - `_create_swarm()` → `_create_graph()` 변경
    - `GraphBuilder`로 노드(router, rag_node, data_expert, sql_node, response_node) 추가
    - 조건부 엣지 연결 (데이터 분석/정보 조회 분기, SQL 재시도 루프)
    - `set_entry_point("router")`, `set_max_node_executions(15)`, `reset_on_revisit(True)` 설정
    - `stream_response` 메서드: Graph 실행을 백그라운드 스레드에서 실행하고 이벤트 큐를 통해 스트리밍
    - 기존 인터페이스 메서드 유지: `get_ui_state`, `enable_debug_mode`, `get_debug_info`, `get_workflow_status`, `get_mcp_client`, `is_mcp_client_active`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 4.2, 4.3, 7.1, 7.2, 7.3, 7.4, 7.5_
  - [ ]* 5.3 조건부 엣지 함수 단위 테스트 작성
    - 각 조건 함수에 대해 True/False 케이스 테스트
    - _Requirements: 2.5, 3.1, 3.2, 3.3, 3.4_

- [ ] 6. EventAdapter 업데이트
  - [ ] 6.1 `agents/multi_agent/event_adapter.py`의 `SwarmEventAdapter`를 업데이트:
    - `AGENT_DISPLAY_NAMES`에 `router`, `response_node` 추가
    - `AGENT_STATUS_MESSAGES`에 새 노드 상태 메시지 추가
    - Router Node는 LLM이 아니므로 스트리밍 이벤트 없음 처리
    - 기존 변환 로직은 Graph와 Swarm이 동일한 이벤트 타입을 사용하므로 대부분 재사용
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_
  - [ ]* 6.2 EventAdapter 속성 기반 테스트 작성
    - **Property 4: 이벤트 변환 형식 정확성** - 임의 Graph 이벤트에 대해 유효한 Streamlit 이벤트 형식
    - **Property 5: 노드 추적 일관성** - 이벤트 시퀀스에 대해 current_agent 일관성
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5**

- [ ] 7. Swarm 코드 정리
  - [ ] 7.1 `agents/multi_agent/lead_agent.py`에서 Swarm 전용 코드 제거 또는 파일 삭제 (ResponseNode로 대체됨). `agents/multi_agent/__init__.py` 업데이트하여 새 모듈 export 추가 및 제거된 모듈 정리. `agents/multi_agent/shared_context.py`에서 `SwarmConfig` 제거
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [ ] 8. Checkpoint - 통합 검증
  - 모든 테스트 통과 확인, 사용자에게 질문이 있으면 문의

- [ ] 9. 기존 테스트 업데이트
  - [ ] 9.1 `tests/` 디렉토리의 기존 테스트 파일 업데이트:
    - `test_lead_agent.py` → 삭제 또는 ResponseNode 테스트로 대체
    - `test_event_adapter.py` → Graph 이벤트 변환 테스트로 업데이트
    - `test_interface_compatibility.py` → Graph 기반 인터페이스 호환성 테스트로 업데이트
    - `test_integration_workflow.py` → Graph 워크플로우 통합 테스트로 업데이트
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [ ] 10. Final Checkpoint - 전체 테스트 통과 확인
  - 모든 테스트 통과 확인, 사용자에게 질문이 있으면 문의

## Notes

- `*` 표시된 태스크는 선택적이며 빠른 MVP를 위해 건너뛸 수 있습니다
- 각 태스크는 특정 요구사항을 참조하여 추적 가능합니다
- Checkpoint에서 점진적 검증을 수행합니다
- 속성 테스트는 `hypothesis` 라이브러리를 사용하며 최소 100회 반복 실행합니다
