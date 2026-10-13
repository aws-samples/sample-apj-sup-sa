# 프로젝트 구조

## 아키텍처 원칙

### 레이어 분리
프로젝트는 에이전트 레이어와 프론트엔드 레이어를 명확히 분리하며, 공유 이벤트 인프라를 통해 통신합니다:

```
agents/events/          # 공유 이벤트 인프라
    ├── registry.py     # 이벤트 라우팅
    ├── lifecycle.py    # 핸들러
    └── ui/             # UI 상태 관리
         ↑                    ↑
         │                    │
    agents/              app/
    (에이전트 로직)      (UI 렌더링)
```

**핵심 규칙**:
- 에이전트 레이어는 `app/` 디렉토리를 절대 import하지 않음
- 모든 데이터는 이벤트 시스템을 통해 흐름
- 순환 의존성 방지

## 디렉토리 구조

### `/app` - Streamlit UI 레이어
프론트엔드 UI 및 사용자 상호작용 처리

- `main.py`: StreamlitChatApp 메인 클래스 (29줄)
- `config.py`: 중앙 집중식 설정 관리 (166줄)
- `session.py`: Streamlit 세션 상태 관리 (80줄)
- `ui.py`: UI 컴포넌트 렌더링 및 유틸리티 (60줄)
- `chat.py`: 채팅 로직 및 스트리밍 처리 (90줄)
- `env_loader.py`: 환경 변수 로딩 (.env 지원)
- `events/handlers.py`: Streamlit UI 전용 핸들러

### `/agents` - 에이전트 레이어
에이전트 로직 및 이벤트 처리

- `strands_agent.py`: Strands Agent 통합 및 조정
- `my_custom_agent.py`: 커스텀 에이전트 구현 예제
- `multi_agent/`: 멀티 에이전트 Text2SQL 시스템
  - `base_agent.py`: 에이전트 기본 클래스
  - `lead_agent.py`: 리드 에이전트 (작업 조율)
  - `sql_agent.py`: SQL 생성 전문 에이전트
  - `data_expert_agent.py`: 데이터 분석 전문 에이전트
  - `shared_context.py`: 에이전트 간 공유 컨텍스트
  - `event_adapter.py`: 이벤트 시스템 어댑터
  - `multi_agent_text2sql.py`: 멀티 에이전트 오케스트레이션
- `events/`: 공유 이벤트 인프라
  - `registry.py`: 이벤트 핸들러 아키텍처
  - `lifecycle.py`: 라이프사이클/로깅 핸들러
  - `ui/`: UI 매니저 모듈
    - `state.py`: UI 상태 관리
    - `messages.py`: 메시지 스트리밍
    - `cot.py`: Chain of Thought 처리
    - `reasoning.py`: 추론 프로세스 표시
    - `tools.py`: 도구 실행 표시
    - `utils.py`, `placeholders.py`: 유틸리티

### 루트 파일
- `app.py`: 간소화된 진입점 (13줄) - 커스텀 설정으로 앱 실행
- `app_old.py`: 레거시 진입점 (참고용)
- `pyproject.toml`: 프로젝트 설정 및 의존성
- `requirements.txt`: Python 의존성 (선택적)

### `/tests`
- `test_streamlit_flow.py`: UI 플로우 테스트
- `test_thread_safety.py`: 스레드 안전성 테스트
- `test_lead_agent.py`: 리드 에이전트 테스트
- `test_sql_agent.py`: SQL 에이전트 테스트
- `test_data_expert_agent.py`: 데이터 전문가 에이전트 테스트
- `test_event_adapter.py`: 이벤트 어댑터 테스트
- `test_interface_compatibility.py`: 인터페이스 호환성 테스트
- `test_integration_workflow.py`: 통합 워크플로우 테스트

## 이벤트 핸들러 시스템

### 핸들러 우선순위
| 핸들러 | 역할 | 우선순위 |
|--------|------|----------|
| StreamlitUIHandler | UI 업데이트 | 10 (높음) |
| ReasoningHandler | 추론 프로세스 처리 | 30 |
| LifecycleHandler | 라이프사이클 관리 | 50 |
| LoggingHandler | 구조화된 로깅 | 80 |
| DebugHandler | 디버그 정보 수집 | 95 (낮음) |

### UI 매니저 시스템
| 매니저 | 역할 |
|--------|------|
| MessageUIManager | 메시지 스트리밍 및 최종 렌더링 |
| COTUIManager | Chain of Thought 감지 및 필터링 |
| ToolUIManager | 도구 실행 상태 및 결과 표시 |
| ReasoningUIManager | 추론 프로세스 상태 위젯 관리 |

## 코딩 규칙

### 모듈 크기
- 권장: 파일당 80줄 이하 (복잡한 로직 제외)
- 단일 책임 원칙: 각 클래스와 함수는 하나의 명확한 책임

### 코드 스타일
- PEP 8 준수
- 모든 함수와 메서드에 타입 힌트 추가
- Docstring 작성 (Google 스타일)

### 에러 처리
- UI 유틸리티를 통한 통합 에러 처리
- 스트리밍 중 에러 발생 시에도 계속 진행
- 명확한 에러 메시지 표시

### 테스트
- 새 컴포넌트는 테스트와 함께 제출
- 각 모듈에 대한 독립적인 단위 테스트 작성
- 기존 테스트는 모두 통과해야 함
