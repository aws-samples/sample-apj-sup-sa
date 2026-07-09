# 기술 스택

## 빌드 시스템

- **패키지 관리자**: `uv` (Python 패키지 관리 및 가상환경)
- **Python 버전**: 3.12+

## 핵심 라이브러리

### 프레임워크
- **Streamlit** (>=1.50.0): 웹 UI 프레임워크
- **Strands Agents** (>=1.15.0): AWS Bedrock 에이전트 프레임워크
- **Boto3** (>=1.40.39): AWS SDK

### 도구 및 확장
- **strands-agents-tools**: Strands Agent용 도구 모음
- **awslabs.aws-dataprocessing-mcp-server**: AWS 데이터 처리 MCP 서버

## 주요 명령어

### 설치 및 설정
```bash
# 의존성 설치
uv sync

# 환경 변수 설정
cp env/local.env .env
# .env 파일 편집하여 AWS 자격증명 입력
```

### 실행
```bash
# 애플리케이션 실행
uv run streamlit run app.py

# 특정 포트로 실행
uv run streamlit run app.py --server.port 8502
```

### 테스트
```bash
# UI 플로우 테스트
python tests/test_streamlit_flow.py

# 스레드 안전성 테스트
python tests/test_thread_safety.py

# pytest 사용 (설치된 경우)
pytest tests -v
```

### 개발
```bash
# 디버그 모드 활성화
export DEBUG_LOGGING=true
export LOG_LEVEL=DEBUG

# AWS 프로파일 설정
export AWS_PROFILE=your-profile
```

## 환경 변수

필수 환경 변수 (`.env` 파일 또는 시스템 환경 변수):
- `AWS_ACCESS_KEY_ID`: AWS 액세스 키
- `AWS_SECRET_ACCESS_KEY`: AWS 시크릿 키
- `AWS_DEFAULT_REGION`: AWS 리전 (기본: us-west-2)

선택적 환경 변수:
- `DEBUG_LOGGING`: 디버그 로깅 활성화 (true/false)
- `LOG_LEVEL`: 로그 레벨 (INFO, DEBUG, ERROR 등)
- `DEFAULT_MODEL`: 기본 모델 ID
