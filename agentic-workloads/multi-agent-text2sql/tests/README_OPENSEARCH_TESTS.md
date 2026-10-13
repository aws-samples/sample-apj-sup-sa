# OpenSearch 통합 테스트 가이드

## 개요

`test_opensearch_integration.py`는 실제 OpenSearch 인스턴스와 연동하여 RAG Agent의 검색 기능을 테스트합니다.

## 사전 요구사항

### 1. OpenSearch 인스턴스

다음 중 하나의 방법으로 OpenSearch를 준비하세요:

#### 옵션 A: AWS OpenSearch Service
- AWS 콘솔에서 OpenSearch 도메인 생성
- VPC 내부 또는 퍼블릭 액세스 설정
- Fine-grained access control 활성화

#### 옵션 B: 로컬 Docker OpenSearch
```bash
docker run -d \
  -p 9200:9200 -p 9600:9600 \
  -e "discovery.type=single-node" \
  -e "OPENSEARCH_INITIAL_ADMIN_PASSWORD=Amazon123!" \
  --name opensearch-node \
  opensearchproject/opensearch:latest
```

### 2. 인덱스 생성 및 데이터 인덱싱

스키마 문서를 OpenSearch에 인덱싱해야 합니다:

```bash
# indexer.py 스크립트 실행
cd bird-benchmark/scripts
python indexer.py \
  --opensearch-endpoint https://localhost:9200 \
  --opensearch-username admin \
  --opensearch-password Amazon123! \
  --index-name bird-description \
  --markdown-dir ../data/mini_dev/markdown_descriptions
```

## 환경 변수 설정

`.env` 파일에 다음 환경 변수를 설정하세요:

```bash
# OpenSearch Configuration (RAG Agent용)
OPENSEARCH_ENDPOINT=https://localhost:9200
OPENSEARCH_INDEX=bird-description
OPENSEARCH_USERNAME=admin
OPENSEARCH_PASSWORD=Amazon123!
```

또는 테스트 실행 시 환경 변수를 직접 전달:

```bash
export OPENSEARCH_ENDPOINT=https://localhost:9200
export OPENSEARCH_USERNAME=admin
export OPENSEARCH_PASSWORD=Amazon123!
export OPENSEARCH_INDEX=bird-description
```

## 테스트 실행

### 전체 OpenSearch 통합 테스트 실행

```bash
python -m pytest tests/test_opensearch_integration.py -v
```

### 특정 테스트 클래스 실행

```bash
# OpenSearch 연결 테스트만
python -m pytest tests/test_opensearch_integration.py::TestOpenSearchConnection -v

# 스키마 문서 검색 테스트만
python -m pytest tests/test_opensearch_integration.py::TestSchemaDocumentSearch -v

# 하이브리드 검색 성능 테스트만
python -m pytest tests/test_opensearch_integration.py::TestHybridSearch -v
```

### 특정 테스트 메서드 실행

```bash
python -m pytest tests/test_opensearch_integration.py::TestHybridSearch::test_hybrid_search_performance -v -s
```

## 테스트 범위

### 1. OpenSearch 연결 테스트 (TestOpenSearchConnection)
- OpenSearch 클라이언트 초기화
- 연결 상태 확인
- 인덱스 존재 확인

### 2. 임베딩 생성 테스트 (TestEmbeddingGeneration)
- 쿼리 임베딩 생성 (Requirements 1.1)
- 임베딩 캐시 히트
- 임베딩 차원 검증 (1024차원)

### 3. 스키마 문서 검색 테스트 (TestSchemaDocumentSearch)
- 스키마 문서 검색 (Requirements 1.2)
- 검색 결과 메타데이터 포함 (Requirements 1.3)
- 필터를 사용한 검색
- 상위 K개 제한 (최대 10개)
- 관련도 순 정렬

### 4. 하이브리드 검색 테스트 (TestHybridSearch)
- 하이브리드 검색 성능 측정
- 키워드 매칭
- 의미론적 검색 (동의어/유사어)

### 5. 도메인 지식 검색 테스트 (TestDomainKnowledgeSearch)
- 도메인 지식 검색 (Requirements 2.1)
- 비즈니스 로직 포함 확인

### 6. 검색 에러 처리 테스트 (TestSearchErrorHandling)
- 빈 쿼리 검색
- 잘못된 인덱스 검색
- 검색 타임아웃 처리

### 7. 검색 결과 포맷팅 테스트 (TestSearchResultFormatting)
- LLM 친화적 형식으로 포맷팅
- 관련도 점수 포함/제외

## 성능 기준

- **검색 시간**: 5초 이내
- **임베딩 생성**: 1초 이내
- **캐시 히트**: 0.1초 이내

## 문제 해결

### OpenSearch 연결 실패

```
ConnectionError: Connection refused
```

**해결 방법**:
1. OpenSearch가 실행 중인지 확인
2. 엔드포인트 URL이 올바른지 확인
3. 방화벽/보안 그룹 설정 확인

### 인증 실패

```
AuthenticationException: Incorrect username or password
```

**해결 방법**:
1. 사용자명과 비밀번호 확인
2. OpenSearch의 Fine-grained access control 설정 확인

### 인덱스 없음

```
IndexNotFoundException: no such index [schema_docs]
```

**해결 방법**:
1. `indexer.py` 스크립트로 데이터 인덱싱
2. 인덱스 이름이 환경 변수와 일치하는지 확인

### 임베딩 생성 실패

```
ClientError: Could not connect to the endpoint URL
```

**해결 방법**:
1. AWS 자격증명 확인 (`AWS_PROFILE`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
2. AWS Bedrock 서비스 사용 가능 리전 확인 (us-west-2 권장)
3. Bedrock 모델 액세스 권한 확인

## 환경 변수 없이 테스트 스킵

OpenSearch 환경 변수가 설정되지 않으면 모든 테스트가 자동으로 스킵됩니다:

```
SKIPPED (OpenSearch 연결 정보 없음 (OPENSEARCH_ENDPOINT, OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD 필요))
```

이는 정상적인 동작이며, CI/CD 파이프라인에서 OpenSearch가 없는 환경에서도 테스트를 실행할 수 있습니다.

## 참고 자료

- [OpenSearch 공식 문서](https://opensearch.org/docs/latest/)
- [AWS OpenSearch Service](https://aws.amazon.com/opensearch-service/)
- [AWS Bedrock Titan Embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)
