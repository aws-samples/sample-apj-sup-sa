# Implementation Plan: RAG Agent Integration

## Overview

기존 멀티에이전트 Text2SQL 시스템에 RAG Agent를 추가하여 OpenSearch에서 스키마 문서를 검색하고 활용하는 기능을 구현합니다.

## Tasks

- [x] 1. RAG Agent 기본 구조 및 OpenSearch 연동 구현
  - BaseMultiAgent를 상속받는 RAGAgent 클래스 생성
  - OpenSearch 클라이언트 초기화 및 연결 설정
  - AWS Bedrock Titan Embeddings V2 통합
  - 기본 시스템 프롬프트 작성
  - _Requirements: 3.1_

- [ ]* 1.1 RAG Agent Swarm 등록 property 테스트 작성
  - **Property 10: Swarm 등록**
  - **Validates: Requirements 3.1**

- [ ] 2. 벡터 검색 기능 구현
- [x] 2.1 임베딩 생성 기능 구현
  - AWS Bedrock을 사용한 쿼리 임베딩 변환
  - 임베딩 캐싱 메커니즘 구현
  - 에러 처리 및 재시도 로직
  - _Requirements: 1.1_

- [ ]* 2.2 쿼리 임베딩 변환 property 테스트 작성
  - **Property 1: 쿼리 임베딩 변환**
  - **Validates: Requirements 1.1**

- [x] 2.3 OpenSearch k-NN 검색 구현
  - 벡터 유사도 검색 쿼리 작성
  - 하이브리드 검색 (벡터 + 키워드) 구현
  - 필터링 기능 (database, table_name)
  - 상위 10개 결과 반환
  - _Requirements: 1.2, 1.3_

- [ ]* 2.4 상위 10개 문서 반환 property 테스트 작성
  - **Property 2: 상위 10개 문서 반환**
  - **Validates: Requirements 1.2**

- [ ]* 2.5 검색 결과 메타데이터 포함 property 테스트 작성
  - **Property 3: 검색 결과 메타데이터 포함**
  - **Validates: Requirements 1.3**

- [x] 2.6 검색 결과 처리 및 에러 핸들링
  - 빈 결과 처리 및 대안 제안
  - 검색 실패 시 에러 처리
  - 타임아웃 처리
  - _Requirements: 1.4_

- [ ]* 2.7 빈 결과 처리 property 테스트 작성
  - **Property 4: 빈 결과 처리**
  - **Validates: Requirements 1.4**

- [x] 3. ~~스키마 정보 추출 및 구조화~~ (불필요 - OpenSearch에 이미 구조화된 청크로 저장됨)
  - **발견**: indexer.py가 이미 청킹 및 구조화 수행
  - **결과**: content 필드에 이미 파싱된 텍스트 포함
  - **메타데이터**: table, database, chunk_type, column_name 필드로 구조화됨
  - **결론**: LLM에 검색 결과를 그대로 전달하면 됨

- [x] 4. 공유 컨텍스트 통합
- [x] 4.1 AnalysisContext 확장
  - rag_schema_results 필드 추가 ✅
  - rag_domain_results 필드 추가 ✅
  - rag_enabled 플래그 추가 ✅
  - 검색 결과 저장 및 조회 메서드 구현 ✅ (add_rag_schema_result, add_rag_domain_result)
  - _Requirements: 3.4_

- [x] 4.2 save_to_context() 메서드 수정
  - 현재: business_intent에 임시 저장 (잘못됨)
  - 수정: context.add_rag_schema_result() 사용
  - _Requirements: 3.4_

- [ ]* 4.3 공유 컨텍스트 저장 property 테스트 작성
  - **Property 13: 공유 컨텍스트 저장**
  - **Validates: Requirements 3.4**

- [x] 5. 기존 에이전트와의 통합
- [x] 5.1 Data Expert Agent 수정
  - RAG Agent 호출 로직 추가 (선택적)
  - 검색된 스키마 문서를 LLM 컨텍스트에 포함
  - handoff_to_agent 도구 사용
  - _Requirements: 3.2_

- [ ]* 5.2 Data Expert 협업 property 테스트 작성
  - **Property 11: Data Expert 협업**
  - **Validates: Requirements 3.2**

- [x] 5.3 SQL Agent 수정
  - RAG Agent 호출 로직 추가 (선택적)
  - 검색된 도메인 지식을 시스템 프롬프트에 포함
  - handoff_to_agent 도구 사용
  - _Requirements: 3.3_

- [ ]* 5.4 SQL Agent 협업 property 테스트 작성
  - **Property 12: SQL Agent 협업**
  - **Validates: Requirements 3.3**

- [x] 5.5 Lead Agent 수정
  - RAG Agent를 Swarm에 등록
  - invocation_state에 OpenSearch 설정 추가
  - RAG 실패 시 기존 워크플로우 계속 진행 로직
  - _Requirements: 3.1, 3.5_

- [ ]* 5.6 실패 시 워크플로우 계속 property 테스트 작성
  - **Property 14: 실패 시 워크플로우 계속**
  - **Validates: Requirements 3.5**

- [-] 6. 불필요한 코드 제거
- [x] 6.1 extract_schema_info() 메서드 제거
  - _parse_markdown_schema() 제거
  - _parse_columns_from_content() 제거
  - SchemaInfo, ColumnDetail 데이터 모델 제거 (또는 사용하지 않음)
  - extract_domain_mappings() 제거
  - DomainMapping 데이터 모델 제거
  - format_results_for_agent() 간소화

- [x] 6.2 검색 결과를 LLM 친화적 형식으로 변환하는 메서드 추가
  - format_search_results_for_llm() 구현
  - content와 메타데이터를 그대로 사용
  - 관련도 점수 포함

- [x] 7. 첫 번째 체크포인트 - 모든 테스트 통과 확인
  - 모든 테스트가 통과하는지 확인하고, 문제가 있으면 사용자에게 질문

- [x] 8. 에러 처리 및 복구 로직 구현 (완료)
- [x] 8.1 OpenSearch 연결 에러 처리
  - 연결 실패 시 재시도 로직 (최대 3회)
  - 자격증명 확인 및 에러 메시지
  - RAG 비활성화 모드로 전환
  - _Requirements: 3.5_

- [x] 8.2 임베딩 생성 에러 처리
  - AWS Bedrock 연결 확인
  - 쿼리 길이 검증 (최대 8192 토큰)
  - 실패 시 RAG 없이 진행
  - _Requirements: 3.5_

- [x] 8.3 검색 결과 없음 처리
  - 대안 검색어 제안 로직
  - 유사 문서 추천
  - 기존 워크플로우 계속 진행
  - _Requirements: 1.4_

- [x] 8.4 타임아웃 및 성능 최적화
  - RAG Agent 타임아웃 설정 (5분)
  - 임베딩 캐싱 구현
  - 비동기 검색 처리
  - _Requirements: 3.5_

- [ ]* 8.5 에러 시나리오 통합 테스트 작성
  - OpenSearch 연결 실패 테스트
  - 인덱스 없음 테스트
  - 검색 결과 없음 테스트
  - RAG 타임아웃 테스트

- [x] 9. 통합 테스트 및 검증
- [x] 9.1 End-to-End 워크플로우 테스트
  - 사용자 쿼리 → RAG 검색 → 테이블 매칭 → SQL 생성
  - RAG 없이 동작 확인
  - RAG 실패 시 복구 확인
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 9.2 OpenSearch 통합 테스트
  - 실제 OpenSearch 인스턴스 연동
  - 스키마 문서 검색 테스트
  - 하이브리드 검색 성능 측정
  - _Requirements: 1.1, 1.2, 1.3_

- [ ]* 9.3 통합 테스트 작성
  - Data Expert + RAG 협업 테스트
  - SQL Agent + RAG 협업 테스트
  - 공유 컨텍스트 전달 테스트

- [x] 10. 문서화 및 설정 가이드 작성
- [x] 10.1 OpenSearch 설정 가이드 작성
  - 인덱스 생성 방법
  - 벡터 필드 설정
  - 접근 권한 설정
  - _Requirements: 3.1_

- [x] 10.2 사용 예제 및 코드 샘플 작성
  - RAG Agent 초기화 예제
  - 검색 쿼리 예제
  - 에러 처리 예제
  - _Requirements: 1.1, 1.2, 1.3_

- [x] 11. 최종 체크포인트 - 모든 테스트 통과 확인
  - 모든 테스트가 통과하는지 확인하고, 문제가 있으면 사용자에게 질문

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
- RAG Agent는 선택적 기능으로, 실패해도 전체 시스템은 정상 동작해야 함
