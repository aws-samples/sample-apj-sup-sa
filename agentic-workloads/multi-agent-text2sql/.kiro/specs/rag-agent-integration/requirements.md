# Requirements Document

## Introduction

기존 멀티에이전트 Text2SQL 시스템에 RAG(Retrieval-Augmented Generation) Agent를 추가하여 데이터베이스 스키마 문서, 도메인 지식, 예제 쿼리 등을 검색하고 활용함으로써 더 정확하고 최적화된 SQL 생성을 지원합니다.

## Glossary

- **RAG_Agent**: 벡터 데이터베이스에서 관련 문서를 검색하여 다른 에이전트에게 컨텍스트를 제공하는 전문가
- **Vector_Database**: 임베딩 벡터로 변환된 문서를 저장하고 유사도 검색을 수행하는 데이터베이스
- **Schema_Documentation**: 데이터베이스 테이블, 컬럼, 관계에 대한 상세 설명 문서
- **Domain_Knowledge**: 비즈니스 용어, 메트릭 정의, 데이터 사용 패턴 등의 도메인 특화 지식
- **Example_Query**: 과거 성공적으로 실행된 쿼리와 그 설명
- **Embedding**: 텍스트를 고차원 벡터로 변환한 표현

## Requirements

### Requirement 1

**User Story:** 사용자로서, 데이터베이스 스키마 문서를 자동으로 검색하여 더 정확한 테이블과 컬럼 정보를 얻고 싶습니다.

#### Acceptance Criteria

1. WHEN RAG_Agent가 스키마 검색 요청을 받으면 THE RAG_Agent SHALL 사용자 쿼리를 임베딩으로 변환한다
2. WHEN RAG_Agent가 벡터 검색을 수행하면 THE RAG_Agent SHALL 유사도 기반으로 상위 10개의 관련 스키마 문서를 반환한다
3. WHEN RAG_Agent가 검색 결과를 반환하면 THE RAG_Agent SHALL 각 문서의 관련도 점수와 메타데이터를 포함한다
4. IF 검색 결과가 없으면 THEN THE RAG_Agent SHALL 빈 결과와 함께 대안 검색 제안을 제공한다
5. WHEN RAG_Agent가 스키마 문서를 처리하면 THE RAG_Agent SHALL 테이블명, 컬럼명, 데이터 타입, 설명을 구조화된 형식으로 추출한다

### Requirement 2

**User Story:** 데이터 분석가로서, 도메인 특화 지식을 활용하여 비즈니스 용어를 올바른 데이터베이스 컬럼으로 매핑하고 싶습니다.

#### Acceptance Criteria

1. WHEN RAG_Agent가 도메인 지식 검색 요청을 받으면 THE RAG_Agent SHALL 비즈니스 용어와 관련된 도메인 문서를 검색한다
2. WHEN RAG_Agent가 메트릭 정의를 검색하면 THE RAG_Agent SHALL 계산 공식, 사용되는 컬럼, 필터 조건을 포함한 정보를 반환한다
3. WHEN RAG_Agent가 용어 매핑을 수행하면 THE RAG_Agent SHALL 비즈니스 용어를 데이터베이스 컬럼명으로 변환하는 매핑 정보를 제공한다
4. IF 여러 개의 매핑 후보가 있으면 THEN THE RAG_Agent SHALL 각 후보의 사용 빈도와 컨텍스트를 함께 제공한다
5. WHEN RAG_Agent가 도메인 지식을 반환하면 THE RAG_Agent SHALL 출처 문서와 신뢰도 점수를 포함한다

### Requirement 3

**User Story:** 시스템 관리자로서, RAG Agent가 다른 에이전트들과 효율적으로 협업하는 아키텍처를 원합니다.

#### Acceptance Criteria

1. WHEN Multi_Agent_System이 RAG_Agent를 초기화하면 THE Multi_Agent_System SHALL RAG_Agent를 Swarm에 등록하고 handoff 도구를 제공한다
2. WHEN Data_Expert_Agent가 테이블 매칭을 수행하면 THE Data_Expert_Agent SHALL RAG_Agent에게 스키마 문서 검색을 요청할 수 있다
3. WHEN SQL_Agent가 쿼리를 생성하면 THE SQL_Agent SHALL RAG_Agent에게 예제 쿼리 검색을 요청할 수 있다
4. WHEN RAG_Agent가 검색 결과를 반환하면 THE RAG_Agent SHALL 공유 컨텍스트에 검색 결과를 저장하여 다른 에이전트가 접근할 수 있게 한다
5. IF RAG_Agent 실행이 실패하면 THEN THE Multi_Agent_System SHALL RAG 없이 기존 워크플로우를 계속 진행한다
