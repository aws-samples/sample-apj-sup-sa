# Requirements Document

## Introduction

현재 단일 text2sql 에이전트가 담당하고 있는 모든 기능을 3개의 전문화된 에이전트로 분리하여 협업하는 멀티에이전트 시스템을 구축합니다. 각 에이전트는 특정 역할에 집중하여 더 나은 성능과 유지보수성을 제공합니다.

## Glossary

- **Lead Agent**: 사용자 요청을 받아 전체 워크플로우를 조정하고 다른 에이전트들에게 작업을 위임하는 중앙 조정자
- **Data Expert Agent**: AWS Athena 데이터 카탈로그를 탐색하고 분석하여 적절한 데이터베이스와 테이블을 식별하는 전문가
- **SQL Agent**: 비즈니스 요구사항을 SQL 쿼리로 변환하고 실행하는 전문가
- **Multi-Agent System**: 여러 자율적인 에이전트가 상호작용하여 복잡한 목표를 달성하는 시스템
- **Agent as Tool Pattern**: Strands 에이전트를 도구로 래핑하여 다른 에이전트가 사용할 수 있게 하는 패턴
- **Swarm Pattern**: 에이전트들이 자율적으로 작업을 전달하며 협업하는 Strands 멀티에이전트 패턴

## Requirements

### Requirement 1

**User Story:** 사용자로서, 자연어로 데이터 분석 요청을 하면 여러 전문 에이전트가 협업하여 정확한 결과를 제공받고 싶습니다.

#### Acceptance Criteria

1. WHEN 사용자가 자연어 쿼리를 입력하면 THE Lead_Agent SHALL 요청을 분석하고 적절한 전문 에이전트에게 작업을 위임한다
2. WHEN 멀티에이전트 작업이 완료되면 THE Lead_Agent SHALL 모든 에이전트의 결과를 통합하여 사용자에게 최종 응답을 제공한다
3. WHEN 에이전트 간 협업이 필요하면 THE Multi_Agent_System SHALL 공유 컨텍스트를 통해 정보를 전달한다
4. IF 에이전트 실행 중 에러가 발생하면 THEN THE Lead_Agent SHALL 사용자에게 명확한 오류 메시지와 다음 단계를 제안한다
5. WHEN 사용자가 작업 진행 상황을 확인하면 THE Lead_Agent SHALL 현재 작업 중인 에이전트 정보를 상태로 표시한다

### Requirement 2

**User Story:** 데이터 분석가로서, 데이터 카탈로그 탐색이 전문화된 에이전트에 의해 수행되어 더 정확한 테이블 식별을 받고 싶습니다.

#### Acceptance Criteria

1. WHEN Data_Expert_Agent가 데이터 탐색 요청을 받으면 THE Data_Expert_Agent SHALL MCP 도구를 사용하여 AWS Athena 카탈로그의 데이터베이스 목록을 조회한다
2. WHEN Data_Expert_Agent가 테이블 목록을 조회하면 THE Data_Expert_Agent SHALL 각 데이터베이스에서 최대 50개의 테이블 메타데이터를 수집한다
3. WHEN Data_Expert_Agent가 비즈니스 요구사항과 테이블을 매칭하면 THE Data_Expert_Agent SHALL Strands Agent의 LLM을 통해 테이블 스키마와 컬럼 정보를 분석하여 가장 적합한 테이블을 추천한다
4. IF 비즈니스 요구사항에 적합한 테이블이 없으면 THEN THE Data_Expert_Agent SHALL 사용 가능한 테이블 목록과 대안을 제시한다
5. WHEN Data_Expert_Agent가 테이블 메타데이터를 분석하면 THE Data_Expert_Agent SHALL 파티션 키와 날짜 컬럼 정보를 식별하여 SQL 최적화 힌트를 제공한다

### Requirement 3

**User Story:** 개발자로서, SQL 생성과 실행이 전문화된 에이전트에 의해 처리되어 더 최적화된 쿼리를 받고 싶습니다.

#### Acceptance Criteria

1. WHEN SQL_Agent가 Data_Expert_Agent로부터 카탈로그 정보를 받으면 THE SQL_Agent SHALL 테이블 스키마, 컬럼 정보, 파티션 키를 시스템 프롬프트 컨텍스트에 포함한다
2. WHEN SQL_Agent가 사용자 자연어 쿼리와 카탈로그 정보를 받으면 THE SQL_Agent SHALL Strands Agent의 LLM을 통해 비즈니스 의도를 해석하고 Athena SQL 쿼리를 생성한다
3. WHEN SQL_Agent가 쿼리를 실행하면 THE SQL_Agent SHALL MCP 도구의 start_query_execution을 호출하고 QueryExecutionId를 저장한다
4. WHEN SQL_Agent가 쿼리 실행 상태를 확인하면 THE SQL_Agent SHALL 5초 간격으로 최대 5회 폴링하여 SUCCEEDED 상태를 대기한다
5. WHEN 쿼리 실행이 성공하면 THE SQL_Agent SHALL get_query_results를 호출하여 최대 1000행의 결과를 반환한다

### Requirement 4

**User Story:** 시스템 관리자로서, 각 에이전트가 독립적으로 동작하면서도 효율적으로 협업하는 아키텍처를 원합니다.

#### Acceptance Criteria

1. WHEN Multi_Agent_System이 초기화되면 THE Multi_Agent_System SHALL Strands Swarm 패턴을 사용하여 에이전트 간 협업을 구성한다
2. WHEN 에이전트가 다른 에이전트로 작업을 전달하면 THE Multi_Agent_System SHALL handoff_to_agent 도구를 사용하여 다음 에이전트로 제어를 이동한다
3. WHEN 에이전트 간 공유 상태가 필요하면 THE Multi_Agent_System SHALL invocation_state를 통해 컨텍스트와 설정을 전파한다
4. IF 에이전트 실행이 실패하거나 무한 루프가 감지되면 THEN THE Multi_Agent_System SHALL 타임아웃과 핸드오프 제한을 통해 실행을 중단한다
5. WHEN 전체 워크플로우가 완료되면 THE Multi_Agent_System SHALL 모든 에이전트의 작업 결과를 통합하여 반환한다

### Requirement 5

**User Story:** 사용자로서, 기존 단일 에이전트와 동일한 인터페이스로 멀티에이전트 시스템을 사용하고 싶습니다.

#### Acceptance Criteria

1. WHEN 사용자가 기존 MyCustomAgent 인터페이스를 호출하면 THE Lead_Agent SHALL 동일한 stream_response 메서드를 제공한다
2. WHEN 사용자가 UI 상태를 조회하면 THE Lead_Agent SHALL 기존과 동일한 get_ui_state 메서드를 제공한다
3. WHEN 이벤트 처리가 필요하면 THE Lead_Agent SHALL 기존 이벤트 시스템과 호환되는 콜백을 제공한다
4. WHEN 사용자가 디버그 모드를 활성화하면 THE Lead_Agent SHALL 모든 에이전트의 디버그 정보를 통합하여 표시한다
5. WHEN Lead_Agent가 MCP 클라이언트를 사용하면 THE Lead_Agent SHALL AWS 데이터 처리 도구에 대한 접근을 관리한다