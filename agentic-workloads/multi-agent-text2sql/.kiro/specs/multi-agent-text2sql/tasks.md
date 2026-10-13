# Implementation Plan

- [x] 1. 프로젝트 구조 설정 및 핵심 인터페이스 구현
  - 멀티에이전트 디렉토리 구조 생성
  - 기본 에이전트 클래스 인터페이스 정의
  - Swarm 설정 및 공유 상태 모델 구현
  - _Requirements: 4.1, 4.3_

- [ ]* 1.1 프로젝트 구조 설정 property 테스트 작성
  - **Property 1: Lead Agent 작업 위임 일관성**
  - **Validates: Requirements 1.1**

- [-] 2. Data Expert Agent 구현
- [x] 2.1 Data Expert Agent 핵심 기능 구현 (LLM 기반)
  - MCP 도구를 사용한 AWS Athena 카탈로그 조회 기능 구현
  - 테이블 메타데이터 수집 로직 구현 (최대 50개/DB)
  - Strands Agent LLM을 통한 테이블 매칭 (rule-based 로직 제거)
  - 파티션 키 및 최적화 힌트 제공
  - _Requirements: 2.1, 2.2, 2.3, 2.5_

- [ ]* 2.2 Data Expert Agent property 테스트 작성
  - **Property 6: 데이터 카탈로그 조회 수행**
  - **Validates: Requirements 2.1**

- [ ]* 2.3 테이블 메타데이터 수집 제한 property 테스트 작성
  - **Property 7: 테이블 메타데이터 수집 제한 준수**
  - **Validates: Requirements 2.2**

- [ ]* 2.4 LLM 기반 테이블 추천 property 테스트 작성
  - **Property 8: LLM 기반 테이블 추천**
  - **Validates: Requirements 2.3**

- [ ]* 2.5 메타데이터 분석 최적화 힌트 property 테스트 작성
  - **Property 9: 메타데이터 분석 최적화 힌트 제공**
  - **Validates: Requirements 2.5**

- [-] 3. SQL Agent 구현
- [x] 3.1 SQL Agent 핵심 기능 구현 (LLM 기반)
  - 카탈로그 정보를 시스템 프롬프트에 동적으로 포함하는 로직 구현
  - Strands Agent LLM을 통한 SQL 쿼리 생성 (rule-based 로직 제거)
  - Athena 쿼리 실행 및 상태 모니터링 구현
  - 결과 조회 및 포맷팅 기능 구현
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ]* 3.2 카탈로그 정보 컨텍스트 포함 property 테스트 작성
  - **Property 10: 카탈로그 정보 컨텍스트 포함**
  - **Validates: Requirements 3.1**

- [ ]* 3.3 LLM 기반 SQL 쿼리 생성 property 테스트 작성
  - **Property 11: LLM 기반 SQL 쿼리 생성**
  - **Validates: Requirements 3.2**

- [ ]* 3.4 쿼리 실행 ID 저장 property 테스트 작성
  - **Property 12: 쿼리 실행 ID 저장**
  - **Validates: Requirements 3.3**

- [ ]* 3.5 폴링 규칙 준수 property 테스트 작성
  - **Property 13: 폴링 규칙 준수**
  - **Validates: Requirements 3.4**

- [ ]* 3.6 결과 행 수 제한 property 테스트 작성
  - **Property 14: 결과 행 수 제한**
  - **Validates: Requirements 3.5**

- [-] 4. Lead Agent 구현
- [x] 4.1 Lead Agent 핵심 기능 구현
  - 사용자 요청 분석 및 의도 파악 로직 구현
  - 에이전트 선택 및 작업 위임 기능 구현
  - 결과 통합 및 응답 생성 로직 구현
  - 에러 처리 및 상태 관리 기능 구현
  - _Requirements: 1.1, 1.2, 1.4, 1.5_

- [ ]* 4.2 결과 통합 완전성 property 테스트 작성
  - **Property 2: 결과 통합 완전성**
  - **Validates: Requirements 1.2**

- [ ]* 4.3 에러 처리 응답 완전성 property 테스트 작성
  - **Property 4: 에러 처리 응답 완전성**
  - **Validates: Requirements 1.4**

- [ ]* 4.4 작업 상태 표시 정확성 property 테스트 작성
  - **Property 5: 작업 상태 표시 정확성**
  - **Validates: Requirements 1.5**

- [-] 5. Swarm 패턴 통합 구현
- [x] 5.1 Swarm 설정 및 에이전트 등록
  - Strands Swarm 인스턴스 생성 및 설정
  - 각 에이전트를 Swarm에 등록
  - handoff_to_agent 도구 통합
  - 공유 컨텍스트 및 invocation_state 설정
  - _Requirements: 4.1, 4.2, 4.3_

- [ ]* 5.2 공유 컨텍스트 정보 전달 property 테스트 작성
  - **Property 3: 공유 컨텍스트 정보 전달**
  - **Validates: Requirements 1.3**

- [ ]* 5.3 작업 전달 도구 사용 property 테스트 작성
  - **Property 15: 작업 전달 도구 사용**
  - **Validates: Requirements 4.2**

- [ ]* 5.4 공유 상태 전파 property 테스트 작성
  - **Property 16: 공유 상태 전파**
  - **Validates: Requirements 4.3**

- [ ]* 5.5 워크플로우 결과 통합 property 테스트 작성
  - **Property 17: 워크플로우 결과 통합**
  - **Validates: Requirements 4.5**

- [x] 6. 기존 인터페이스 호환성 구현
- [x] 6.1 MyCustomAgent 인터페이스 호환성 구현
  - stream_response 메서드 구현 (Swarm 실행 래핑)
  - get_ui_state 메서드 구현
  - 기존 이벤트 시스템과 호환되는 콜백 구현
  - MCP 클라이언트 접근 관리 구현
  - 디버그 모드 통합 구현
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

- [ ]* 6.2 인터페이스 호환성 stream_response property 테스트 작성
  - **Property 18: 인터페이스 호환성 - stream_response**
  - **Validates: Requirements 5.1**

- [ ]* 6.3 인터페이스 호환성 get_ui_state property 테스트 작성
  - **Property 19: 인터페이스 호환성 - get_ui_state**
  - **Validates: Requirements 5.2**

- [ ]* 6.4 이벤트 시스템 호환성 property 테스트 작성
  - **Property 20: 이벤트 시스템 호환성**
  - **Validates: Requirements 5.3**

- [ ]* 6.5 디버그 정보 통합 property 테스트 작성
  - **Property 21: 디버그 정보 통합**
  - **Validates: Requirements 5.4**

- [ ]* 6.6 MCP 클라이언트 접근 관리 property 테스트 작성
  - **Property 22: MCP 클라이언트 접근 관리**
  - **Validates: Requirements 5.5**

- [x] 7. 이벤트 시스템 어댑터 구현
- [x] 7.1 Swarm 이벤트를 Streamlit 이벤트로 변환하는 어댑터 구현
  - Swarm 스트리밍 이벤트 수신 및 변환
  - 기존 이벤트 큐와 통합
  - 에이전트별 이벤트 필터링 및 라우팅
  - UI 상태 업데이트 로직 구현
  - _Requirements: 1.5, 5.3_

- [x] 8. 첫 번째 체크포인트 - 모든 테스트 통과 확인
  - 모든 테스트가 통과하는지 확인하고, 문제가 있으면 사용자에게 질문

- [x] 9. 통합 테스트 및 최적화
- [x] 9.1 전체 워크플로우 통합 테스트 구현
  - 실제 AWS Athena 환경에서의 end-to-end 테스트
  - 다양한 자연어 쿼리 시나리오 테스트
  - 에러 상황 및 복구 시나리오 테스트
  - 성능 및 타임아웃 테스트
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [ ]* 9.2 통합 테스트 작성
  - Swarm 전체 워크플로우 통합 테스트
  - AWS Athena 실제 연동 테스트
  - UI 이벤트 시스템 통합 테스트

- [x] 10. 기존 시스템과의 교체 및 마이그레이션
- [x] 10.1 기존 MyCustomAgent 교체
  - 기존 MyCustomAgent 클래스를 새로운 멀티에이전트 시스템으로 교체
  - 설정 파일 및 초기화 로직 업데이트
  - 기존 기능과의 호환성 검증
  - 성능 비교 및 최적화
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

- [x] 11. 최종 체크포인트 - 모든 테스트 통과 확인
  - 모든 테스트가 통과하는지 확인하고, 문제가 있으면 사용자에게 질문