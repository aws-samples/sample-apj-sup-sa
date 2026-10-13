# AGENTS.md

AI Agent가 Meeting Assistant 저장소에서 작업할 때 필요한 컨텍스트와 규칙입니다.

## Project Overview

Meeting Assistant는 회의 녹음, 실시간 음성 인식, AI 요약을 제공하는 Electron 기반 데스크톱 애플리케이션입니다. AWS Transcribe와 Amazon Bedrock을 활용합니다.

## Reference Docs

| Doc | Content |
|------|------|
| [product.md](.kiro/steering/product.md) | 목적, 사용자, 핵심 기능 |
| [tech.md](.kiro/steering/tech.md) | 기술 스택, 빌드 설정, 명령어 |
| [structure.md](.kiro/steering/structure.md) | 디렉토리 구조, 아키텍처 패턴 |

## Tech Stack

- Runtime: Electron 39.x
- Frontend: React 19.x + TypeScript 5.x (strict)
- Build: Vite 7.x + Electron Forge
- Storage: `electron-store` (암호화 설정), SQLite (`better-sqlite3`)
- AWS: `@aws-sdk/client-transcribe-streaming`, `@aws-sdk/client-bedrock-runtime`
- MCP: `@modelcontextprotocol/sdk`
- Logging: `pino`, `pino-pretty`

## Directory Structure

```
src/
├── main/                    # Electron main process
│   ├── main.ts              # 앱 라이프사이클, 윈도우 관리, Settings IPC
│   ├── constants.ts         # 윈도우 크기, API 버전 상수
│   ├── ipc/                  # IPC 핸들러
│   │   ├── index.ts          # 핸들러 export
│   │   ├── meeting.handlers.ts # Meeting/Audio/Summary IPC
│   │   ├── meeting-prep-format.ts # Meeting prep formatting
│   │   └── mcp.handlers.ts   # MCP 관련 IPC
│   ├── migrations/           # SQLite 마이그레이션
│   │   └── index.ts          # 스키마 버전 관리
│   ├── services/             # 비즈니스 로직 서비스
│   │   ├── logger.service.ts      # Pino 구조화된 로깅
│   │   ├── rate-limiter.service.ts # API 속도 제한
│   │   ├── database.service.ts    # SQLite CRUD
│   │   ├── transcribe.service.ts  # AWS Transcribe 스트리밍
│   │   ├── bedrock.service.ts     # AI 교정/번역/요약
│   │   ├── sentence-buffer.service.ts # 문장 버퍼링
│   │   ├── session-manager.service.ts # 회의 세션 상태
│   │   ├── meeting-streaming.service.ts # 스트리밍 오케스트레이션
│   │   ├── meeting-correction.service.ts # 교정 오케스트레이션
│   │   ├── settings.service.ts    # 설정 관리
│   │   ├── window.service.ts      # 윈도우 관리
│   │   └── mcp-client.service.ts  # MCP 클라이언트
│   └── utils/
│       └── audio-converter.ts # Base64 ↔ Buffer 변환
├── preload/
│   └── preload.ts           # Context bridge, ElectronAPI 타입
├── renderer/                # React frontend
│   ├── App.tsx              # 루트 컴포넌트, 네비게이션
│   ├── main.tsx             # React 엔트리
│   ├── components/
│   │   ├── Sidebar.tsx
│   │   ├── Settings.tsx
│   │   ├── MeetingTypeSelector.tsx
│   │   ├── MeetingView.tsx
│   │   ├── MicrophoneControl.tsx
│   │   ├── QuickMeetingTranscript.tsx
│   │   ├── meeting/         # 회의 UI 컴포넌트
│   │   │   ├── MeetingWorkspace.tsx
│   │   │   ├── MeetingTabbedPanel.tsx
│   │   │   ├── MeetingFloatingBar.tsx
│   │   │   └── MeetingPrepModal.tsx
│   │   └── meeting-types/   # 회의 유형별 뷰
│   │       ├── ClientMeetingView.tsx
│   │       ├── QuickMeetingView.tsx
│   │       ├── EnglishMeetingView.tsx
│   │       └── InterviewMeetingView.tsx
│   ├── hooks/
│   │   ├── useSettings.ts       # 설정 로드/저장
│   │   ├── useMeeting.ts        # 회의 상태 관리
│   │   ├── useTranscription.ts  # 트랜스크립션 이벤트
│   │   ├── useAudioCapture.ts   # 마이크 캡처
│   │   ├── useRecordingControls.ts # 녹음 제어
│   │   ├── useSummary.ts        # 요약 생성
│   │   └── useMeetingHistory.ts # 회의 히스토리 관리
│   ├── workers/
│   │   └── audio-processor.worklet.ts # AudioWorklet
│   ├── utils/
│   │   ├── electron.ts          # electronAPI 헬퍼
│   │   ├── masking.ts           # 민감정보 마스킹
│   │   ├── normalize-error.ts   # 에러 정규화
│   │   ├── transcript-format.ts # 트랜스크립트 포맷팅
│   │   ├── clipboard.ts         # 클립보드 유틸리티
│   │   └── meeting-prep-format.ts # 미팅 준비 포맷팅
│   ├── styles/
│   │   └── global.css           # 전역 스타일
│   └── assets/                  # 폰트, 아이콘, 이미지
└── shared/                  # 프로세스 간 공유
    ├── types/
    │   ├── settings.ts          # AWS, Transcribe, Bedrock 설정
    │   ├── meeting.ts           # Meeting, MeetingType, RecordingState
    │   ├── transcription.ts     # TranscriptionSegment, CorrectedSentence
    │   ├── database.ts          # DB Row 타입
    │   ├── audio.ts             # 오디오 관련 타입
    │   ├── mcp.ts               # MCP 관련 타입
    │   ├── meeting-prep.ts      # 미팅 준비 타입
    │   └── english.ts           # 영어 회의 타입
    └── constants/
        ├── ipc-channels.ts      # IPC 채널 상수
        └── defaults.ts          # 기본값 상수
```

## Commands

```bash
npm start            # Electron 개발 모드
npm run dev:web      # 렌더러만 브라우저에서 실행
npm run build        # TypeScript 컴파일
npm run package      # 현재 플랫폼용 패키징
npm run make         # 인스톨러 생성
```

## IPC Architecture

### 채널 정의
- `src/shared/constants/ipc-channels.ts`에서 모든 채널 상수 정의

### 핸들러 위치
- Settings: `src/main/main.ts` (ipcMain.handle)
- Meeting/Audio/Summary: `src/main/ipc/meeting.handlers.ts`
- MCP: `src/main/ipc/mcp.handlers.ts`

### 렌더러 접근
- `window.electronAPI` (preload에서 expose)
- 타입: `src/preload/preload.ts`의 `ElectronAPI` 인터페이스

### IPC 채널 목록

| Category | Channel | Direction |
|----------|---------|-----------|
| Settings | `settings:save/load/clear/getAWSCredentials` | invoke |
| Meeting | `meeting:create/start/stop/pause/resume/get/list/delete/deleteAll` | invoke |
| Meeting | `meeting:updatePrepData/updateMetadata/getMetadata` | invoke |
| Audio | `audio:chunk` | send (one-way) |
| Transcription | `transcription:partial/final/corrected/error` | on (event) |
| Summary | `summary:generate` | invoke |
| Summary | `summary:complete` | on (event) |
| English | `english:suggestions/translate` | invoke |
| MCP | `mcp:connect/disconnect/getStatus/listTools/callTool` | invoke |

### IPC 추가 시 체크리스트
1. `src/shared/constants/ipc-channels.ts`에 채널 추가
2. `src/preload/preload.ts`에 메서드 및 타입 추가
3. `src/main/ipc/` 또는 `src/main/main.ts`에 핸들러 구현
4. 렌더러에서 `window.electronAPI.xxx()` 호출

## Services

| Service | 역할 |
|---------|------|
| `LoggerService` | Pino 기반 구조화된 로깅 (개발/프로덕션 분리) |
| `RateLimiterService` | API 호출 속도 제한 (슬라이딩 윈도우) |
| `DatabaseService` | SQLite CRUD (meetings, segments, summaries) |
| `TranscribeService` | AWS Transcribe 스트리밍 연결 |
| `BedrockService` | AI 교정, 번역, 요약 (Converse/InvokeModel API) |
| `SentenceBufferService` | 부분 결과를 완전한 문장으로 버퍼링 |
| `SessionManagerService` | 회의 세션 상태 관리 |
| `MeetingStreamingService` | 스트리밍 오케스트레이션 |
| `MeetingCorrectionService` | 교정 오케스트레이션 |
| `SettingsService` | 설정 관리 (암호화 포함) |
| `WindowService` | 윈도우 관리 |
| `McpClientService` | MCP 서버 연결 및 도구 호출 |

## Meeting Types

| Type | ID | 설명 |
|------|-----|------|
| Client Meeting | `client` | 고객 미팅, 액션 아이템, MCP 연동 |
| Quick Meeting | `weekly` | 빠른 싱크, 태스크 추적 |
| English Meeting | `english` | 영어→한국어 번역 |
| Amazon Interview | `interview` | 구조화된 Q&A |

## Security Rules

- `contextIsolation: true`, `nodeIntegration: false` 유지
- AWS 자격 증명은 `safeStorage`로 암호화
- 민감 데이터는 main process에서만 처리
- CSP 헤더 설정 (개발/프로덕션 분리)

## State Management

- 로컬 상태: React `useState`
- 복잡한 로직: Custom hooks로 추출
- 설정 저장: `electron-store` (main process)
- 회의 데이터: SQLite (`better-sqlite3`)

## Styling

- 전역 CSS + CSS custom properties
- Tailwind-like 유틸리티 클래스 네이밍
- Material Symbols 아이콘

## Code Style

### TypeScript/React
- `strict` 모드, `any` 사용 금지
- 컴포넌트: `PascalCase`, hooks: `useXxx`
- 함수/변수: `camelCase`, 상수: `UPPER_SNAKE_CASE`

### Import 순서
1. Node/builtin
2. External packages
3. Internal aliases (`@/`, `@shared/`)
4. Relative paths
5. Styles/assets

### Path Aliases
- `@/*` → `src/*`
- `@shared/*` → `src/shared/*`

### Error Handling
- Main process: IO/native 호출은 try/catch
- Renderer: 사용자 친화적 에러 메시지
- IPC 실패: 의미 있는 에러 반환

### React Guidelines
- 컴포넌트 200줄 초과 시 분리
- `useEffect`에 cleanup 함수 포함
- 복잡한 상태 로직은 custom hook으로

### Electron Main Process
- 파일 시스템/네이티브 작업은 main process에서
- 렌더러는 IPC로 요청
- 장시간 작업은 progress 이벤트 고려

## UI/UX Guidelines

- 상태 메시지: 간결한 한국어
- 버튼 레이블: 동사 우선
- 에러 메시지: 사용자 행동 유도
- 전체/컴팩트 윈도우 레이아웃 확인

## Checklist (Before PR)

- [ ] IPC 흐름 확인: shared → preload → main → renderer
- [ ] `contextIsolation`, `nodeIntegration` 설정 유지
- [ ] 민감 데이터 암호화 확인
- [ ] 타입 정의 완전성 확인
- [ ] 에러 핸들링 구현
