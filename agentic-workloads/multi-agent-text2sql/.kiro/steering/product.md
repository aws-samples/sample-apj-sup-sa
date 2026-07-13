# 제품 개요

**Strands Agent + Streamlit 통합 멀티에이전트 Text2SQL 시스템**

AWS Bedrock의 Strands Agent를 활용한 멀티에이전트 시스템으로, 자연어 쿼리를 AWS Athena SQL로 변환하고 실행합니다. 3개의 전문화된 에이전트(Lead, Data Expert, SQL)가 Swarm 패턴으로 협업합니다.

## 핵심 기능

### 멀티에이전트 Text2SQL 시스템
- **Lead Agent**: 사용자 요청 분석, 워크플로우 조정, 결과 통합
- **Data Expert Agent**: AWS Athena 카탈로그 탐색, 테이블 식별, 메타데이터 분석
- **SQL Agent**: LLM 기반 SQL 생성, Athena 쿼리 실행, 결과 조회
- **Swarm 패턴**: `handoff_to_agent` 도구로 에이전트 간 작업 전달
- **공유 컨텍스트**: `AnalysisContext`를 통한 에이전트 간 정보 공유

### UI 및 스트리밍
- **실시간 스트리밍**: 버퍼링된 텍스트 스트리밍
- **도구 실행 시각화**: 도구 실행 과정을 상태 위젯으로 표시
- **Chain of Thought**: `<thinking>` 블록 자동 감지 및 별도 위젯 표시
- **작업 상태 표시**: 현재 작업 중인 에이전트 정보 실시간 표시

### 아키텍처
- **기존 인터페이스 호환**: `MyCustomAgent`와 동일한 `stream_response`, `get_ui_state` 제공
- **이벤트 어댑터**: Swarm 이벤트를 Streamlit 이벤트로 변환
- **MCP 통합**: AWS 데이터 처리 MCP 서버 연동

## 사용 사례

- 자연어로 AWS Athena 데이터 분석 요청
- 복잡한 비즈니스 쿼리의 자동 SQL 변환
- 데이터 카탈로그 탐색 및 테이블 추천
