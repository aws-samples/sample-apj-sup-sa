# 요구사항 문서: Swarm → Graph 패턴 마이그레이션

## 소개

현재 Strands Swarm 패턴 기반의 멀티에이전트 Text2SQL 시스템을 Strands SDK의 Graph 패턴으로 전환합니다. LLM 기반 라우팅(`handoff_to_agent`)을 결정론적 그래프 워크플로우로 대체하여 토큰 소비를 줄이고 실행 경로를 예측 가능하게 만듭니다.

## 용어집

- **Graph**: Strands SDK의 `Graph` 클래스를 사용한 결정론적 방향 그래프 오케스트레이션 패턴
- **GraphBuilder**: `Graph` 인스턴스를 구성하기 위한 빌더 패턴 클래스
- **GraphNode**: 그래프 내 개별 실행 단위 (에이전트 또는 커스텀 노드)
- **GraphEdge**: 노드 간 연결, 선택적 조건 함수 포함
- **GraphState**: 그래프 실행 중 공유되는 상태 객체 (completed_nodes, results 등)
- **Router_Node**: 사용자 요청 유형을 규칙 기반으로 분류하는 결정론적 노드
- **RAG_Node**: 벡터 데이터베이스에서 스키마 문서와 도메인 지식을 검색하는 노드
- **Data_Expert_Node**: AWS Athena 카탈로그를 탐색하고 테이블을 식별하는 노드
- **SQL_Node**: SQL 쿼리를 생성하고 Athena에서 실행하는 노드
- **Response_Node**: 최종 결과를 사용자 친화적으로 포맷팅하는 노드
- **MultiAgentText2SQL**: 시스템의 최상위 오케스트레이터 클래스
- **AnalysisContext**: 노드 간 공유되는 분석 컨텍스트 데이터 구조
- **EventAdapter**: Graph 이벤트를 Streamlit UI 이벤트로 변환하는 어댑터
- **Swarm**: 기존 LLM 기반 `handoff_to_agent` 라우팅 패턴 (제거 대상)

## 요구사항

### Requirement 1: 규칙 기반 Router Node

**User Story:** 개발자로서, 사용자 요청을 LLM 없이 규칙 기반으로 분류하고 싶다. 그래야 토큰 소비 없이 빠르게 워크플로우 경로를 결정할 수 있다.

#### Acceptance Criteria

1. WHEN 사용자 입력이 수신되면, THE Router_Node SHALL 키워드 매칭 규칙을 사용하여 요청을 "데이터 분석" 또는 "정보 조회" 유형으로 분류한다
2. WHEN 요청에 SQL 실행이 필요한 키워드(합계, 통계, 평균, 비교, 추이 등)가 포함되면, THE Router_Node SHALL 요청 유형을 "데이터 분석"으로 분류한다
3. WHEN 요청에 메타데이터 조회 키워드(테이블 목록, 스키마, 컬럼 정보, 어떤 데이터 등)가 포함되면, THE Router_Node SHALL 요청 유형을 "정보 조회"로 분류한다
4. WHEN 요청 유형이 명확하지 않으면, THE Router_Node SHALL 기본값으로 "데이터 분석" 유형을 반환한다
5. THE Router_Node SHALL LLM 호출 없이 순수 Python 로직으로 분류를 수행한다

### Requirement 2: Graph 워크플로우 구성

**User Story:** 개발자로서, GraphBuilder를 사용하여 결정론적 워크플로우를 구성하고 싶다. 그래야 실행 경로가 예측 가능하고 디버깅이 쉬워진다.

#### Acceptance Criteria

1. THE MultiAgentText2SQL SHALL Strands SDK의 `GraphBuilder`를 사용하여 노드와 엣지를 구성한다
2. THE MultiAgentText2SQL SHALL Router_Node를 그래프의 진입점(entry point)으로 설정한다
3. WHEN "데이터 분석" 유형이면, THE Graph SHALL Router → RAG → Data_Expert → SQL → Response 순서로 노드를 실행한다
4. WHEN "정보 조회" 유형이면, THE Graph SHALL Router → RAG → Data_Expert → Response 순서로 노드를 실행하고 SQL_Node를 건너뛴다
5. THE Graph SHALL 조건부 엣지(condition 함수)를 사용하여 Data_Expert_Node 이후 SQL_Node 또는 Response_Node로 분기한다
6. THE Graph SHALL `max_node_executions` 설정으로 무한 루프를 방지한다
7. THE Graph SHALL `reset_on_revisit=True`를 설정하여 재방문 시 노드 상태를 초기화한다

### Requirement 3: SQL 재시도 루프

**User Story:** 개발자로서, SQL 실행 실패 시 자동으로 재시도하고 싶다. 그래야 일시적인 오류로 인한 전체 워크플로우 실패를 방지할 수 있다.

#### Acceptance Criteria

1. WHEN SQL_Node에서 구문 오류 또는 스키마 오류가 발생하면, THE SQL_Node SHALL 자체적으로 최대 2회 재시도한다
2. WHEN SQL_Node에서 테이블 또는 컬럼 부족 오류가 발생하면, THE Graph SHALL Data_Expert_Node로 돌아가서 추가 탐색 후 다시 SQL_Node를 실행한다
3. WHEN SQL_Node가 2회 재시도 후에도 실패하면, THE Graph SHALL Response_Node로 에러 응답을 전달한다
4. THE Graph SHALL Data_Expert → SQL 재시도 루프를 최대 1회로 제한한다

### Requirement 4: RAG Node 통합 및 Fallback

**User Story:** 개발자로서, RAG 검색이 실패해도 워크플로우가 계속 진행되길 원한다. 그래야 RAG 서비스 장애가 전체 시스템에 영향을 주지 않는다.

#### Acceptance Criteria

1. THE RAG_Node SHALL 기존 RAGAgent의 검색 기능을 그대로 사용하여 스키마 문서와 도메인 지식을 검색한다
2. WHEN RAG_Node에서 오류가 발생하면, THE Graph SHALL Data_Expert_Node로 바로 진행한다
3. WHEN RAG가 비활성화 상태이면, THE Graph SHALL RAG_Node를 건너뛰고 Data_Expert_Node로 직접 진행한다
4. THE RAG_Node SHALL 검색 결과를 GraphState를 통해 후속 노드에 전달한다

### Requirement 5: 기존 에이전트 재사용

**User Story:** 개발자로서, 기존 RAGAgent, DataExpertAgent, SQLAgent의 핵심 로직을 그대로 재사용하고 싶다. 그래야 마이그레이션 리스크를 최소화할 수 있다.

#### Acceptance Criteria

1. THE Data_Expert_Node SHALL 기존 DataExpertAgent의 Strands Agent 인스턴스를 그래프 노드의 executor로 사용한다
2. THE SQL_Node SHALL 기존 SQLAgent의 Strands Agent 인스턴스를 그래프 노드의 executor로 사용한다
3. THE RAG_Node SHALL 기존 RAGAgent의 Strands Agent 인스턴스를 그래프 노드의 executor로 사용한다
4. WHEN 기존 에이전트가 그래프 노드로 사용되면, THE 에이전트 SHALL `handoff_to_agent` 관련 로직을 시스템 프롬프트에서 제거한다
5. THE 각 에이전트 SHALL 이전 노드의 출력을 입력으로 받아 처리한다

### Requirement 6: Response Node

**User Story:** 개발자로서, 최종 결과를 사용자 친화적으로 포맷팅하는 전용 노드가 필요하다. 그래야 응답 품질이 일관되고 Lead Agent의 역할을 대체할 수 있다.

#### Acceptance Criteria

1. THE Response_Node SHALL 이전 노드들의 결과를 통합하여 사용자 친화적인 응답을 생성한다
2. WHEN 데이터 분석 결과가 전달되면, THE Response_Node SHALL 실행된 SQL과 결과 데이터를 포함한 응답을 생성한다
3. WHEN 정보 조회 결과가 전달되면, THE Response_Node SHALL 테이블 및 컬럼 정보를 정리하여 응답을 생성한다
4. WHEN 에러가 전달되면, THE Response_Node SHALL 실패 원인과 가능한 조치를 포함한 응답을 생성한다

### Requirement 7: 기존 인터페이스 호환성

**User Story:** 개발자로서, 기존 Streamlit UI 코드를 수정하지 않고 Graph 패턴으로 전환하고 싶다. 그래야 프론트엔드 변경 없이 백엔드만 교체할 수 있다.

#### Acceptance Criteria

1. THE MultiAgentText2SQL SHALL 기존과 동일한 `stream_response(user_input)` 메서드를 제공한다
2. THE MultiAgentText2SQL SHALL 기존과 동일한 `get_ui_state()` 메서드를 제공한다
3. THE `stream_response` SHALL 기존과 동일한 이벤트 딕셔너리 형식(data, current_tool_use, tool_result, reasoningText, type)을 yield한다
4. THE MultiAgentText2SQL SHALL 기존과 동일한 `enable_debug_mode`, `get_debug_info`, `get_workflow_status` 메서드를 제공한다
5. THE MultiAgentText2SQL SHALL 기존과 동일한 MCP 클라이언트 관리 인터페이스(`get_mcp_client`, `is_mcp_client_active`)를 제공한다

### Requirement 8: 이벤트 어댑터 업데이트

**User Story:** 개발자로서, Graph 패턴의 이벤트를 기존 Streamlit UI 이벤트 시스템과 호환되도록 변환하고 싶다. 그래야 UI에서 에이전트 상태를 실시간으로 표시할 수 있다.

#### Acceptance Criteria

1. THE EventAdapter SHALL Graph의 `multiagent_node_start` 이벤트를 기존 에이전트 상태 이벤트로 변환한다
2. THE EventAdapter SHALL Graph의 `multiagent_node_stop` 이벤트를 기존 에이전트 완료 이벤트로 변환한다
3. THE EventAdapter SHALL Graph의 `multiagent_handoff` 이벤트를 기존 에이전트 전환 이벤트로 변환한다
4. THE EventAdapter SHALL Graph의 `multiagent_node_stream` 이벤트에서 텍스트, 도구, 추론 이벤트를 추출하여 기존 형식으로 변환한다
5. THE EventAdapter SHALL 현재 실행 중인 노드 정보를 추적하여 UI에 표시할 수 있도록 한다

### Requirement 9: Swarm 코드 제거

**User Story:** 개발자로서, 더 이상 사용하지 않는 Swarm 관련 코드를 정리하고 싶다. 그래야 코드베이스가 깔끔하게 유지된다.

#### Acceptance Criteria

1. WHEN Graph 마이그레이션이 완료되면, THE MultiAgentText2SQL SHALL Swarm 인스턴스 생성 코드를 제거한다
2. WHEN Graph 마이그레이션이 완료되면, THE LeadAgent SHALL 제거되거나 Response_Node로 대체된다
3. WHEN Graph 마이그레이션이 완료되면, THE 각 에이전트의 시스템 프롬프트 SHALL `handoff_to_agent` 관련 지시사항을 제거한다
4. WHEN Graph 마이그레이션이 완료되면, THE SharedContext SHALL `SwarmConfig` 클래스를 `GraphConfig` 클래스로 대체한다
