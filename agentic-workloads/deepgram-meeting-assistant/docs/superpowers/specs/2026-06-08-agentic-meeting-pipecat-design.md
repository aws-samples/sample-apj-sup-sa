# Agentic Meeting 모드 (Pipecat 기반) 설계

- 작성일: 2026-06-08
- 상태: 설계 확정 (구현 전)
- 대상: Pipecat 해커톤 데모 제출

## 1. 배경 & 목표

Meeting Assistant는 Electron(TypeScript) 데스크톱 앱으로, 현재 4개 회의 모드
(`client`, `weekly`, `translated`, `interview`)를 제공한다. 이 모드들은 모두
**main 프로세스에서 AWS SDK를 직접 호출**한다:

```
Renderer(AudioWorklet, 16kHz mono PCM Int16 → base64)
  → IPC AUDIO_CHUNK
  → Main TranscribeService.addAudioChunk()  (AWS Transcribe streaming)
  → onPartial/onFinal
  → BedrockService (교정/번역/요약)
  → IPC TRANSCRIPTION_* → Renderer 표시
```

**목표**: Pipecat 해커톤 데모를 위해 **`agentic`** 라는 새 회의 모드를 추가한다.
이 모드는 AWS SDK 직접 호출 경로 대신 **로컬에서 사용자가 직접 실행하는 Pipecat
서버(`server/`)** 를 거쳐 STT(AWS Transcribe) + LLM(AWS Bedrock) 파이프라인을
구동한다. 같은 오디오 입력·같은 화면 표시를 재사용하되, 그 사이의 STT+LLM 처리만
Pipecat이 담당한다.

**비목표 (이번 범위 밖)**:
- agentic 고급 기능(실시간 질문/액션아이템 추출, tool-calling/MCP 연동, 음성 Q&A,
  실시간 요약 등)은 이 파이프라인 위에 **후속으로** 얹는다. 이번 spec은 그 토대가 되는
  **"Pipecat 경로로 전사+Bedrock이 흐르는 새 모드 인프라"** 까지만 다룬다.
- Pipecat 서버의 프로세스 번들링/자동 spawn은 다루지 않는다(아래 4.2 참고).

## 2. 핵심 컨셉

기존 4개 모드는 그대로 둔다(무손상). `agentic` 모드만 main 프로세스의 분기에서
AWS 직접 호출 대신 **Pipecat 서버와의 WebSocket 통신**으로 라우팅한다.

```
[Renderer]                    [Electron Main]                 [server/ (사용자가 직접 실행)]
AudioWorklet ──AUDIO_CHUNK──▶ PipecatBridgeService ──WS connect─▶ WebsocketServerTransport :8765
(16kHz PCM)      (IPC)         · WS client only                  → AWSTranscribeSTTService
                              · 연결 안되면 안내 에러             → ContextAggregator.user()
TranscriptionView ◀─IPC───────· WS msg → TRANSCRIPTION_* ◀─WS──  → AWSBedrockLLMService
```

핵심 원칙:
- **Electron은 Pipecat 서버를 spawn하지 않는다.** 사용자가 `cd server && python bot.py`로
  직접 띄운다. Bridge는 순수 WebSocket 클라이언트.
- **AWS 자격증명은 Pipecat 서버가 독립적으로 관리한다(의도된 결정).** Pipecat 서버는
  `server/.env`에서 자체적으로 자격증명을 읽으며, 앱의 암호화 설정(electron-store)과는
  분리된다. 이는 별도의 trust boundary를 만들지만, 본 설계는 **로컬 데모 / 단일 개발자
  환경**을 전제로 하므로 의도적으로 단순한 경로를 택한다(상세 논의·완화책은 5절 참고).
- 결과는 기존 `TRANSCRIPTION_PARTIAL/FINAL/CORRECTED/ERROR` IPC 채널로 그대로 흘려보내
  Renderer 표시 로직을 최대한 재사용한다(매핑 계약은 3.4 참고).

## 3. 아키텍처 (3개 독립 유닛)

### 3.1 Pipecat 서버 (`server/`, Python — 신규)

- 파일 구성:
  - `server/bot.py` — FastAPI + Pipecat 파이프라인 정의
  - `server/requirements.txt` — pipecat-ai[aws], fastapi, uvicorn, websockets 등
  - `server/.env.example` — AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION /
    모델 ID / 언어 설정
  - `server/README.md` — venv 셋업 + 실행 명령
- 책임: WebSocket으로 PCM 오디오를 받아 STT→Bedrock 파이프라인을 돌리고,
  partial/final 전사와 correction 결과를 JSON 메시지로 클라이언트(Electron)에 반환.
- Pipecat 구성 요소(context7 `/pipecat-ai/docs` 교차검증 완료):
  - `AWSTranscribeSTTService` (STT)
  - `AWSBedrockLLMService` (LLM, 모델 ID는 `us.anthropic.claude-*` 등 설정 가능)
  - WebSocket transport: `FastAPIWebsocketTransport` (FastAPI `/ws` 엔드포인트) 또는
    `WebsocketServerTransport`(기본 `localhost:8765`). **구현 시 context7로 커스텀
    serializer/transport 패턴 재확인 후 택일** (3.4 참고).
  - 파이프라인 골격:
    `transport.input() → stt → context_aggregator.user() → llm → transport.output()`
- **AWS 자격증명 소싱**: 서버가 `server/.env`(또는 표준 AWS 환경변수 / 프로파일)에서
  자체 로드한다. 앱과 무관하게 독립 동작하며, Electron은 자격증명을 서버로 전달하지 않는다.

### 3.2 `StreamingBackend` 추상화 + `PipecatBridgeService` (main 프로세스, 신규)

라이프사이클 안전성을 위해, 두 경로(AWS 직접 / Pipecat)를 **하나의 공통 인터페이스**
뒤에 둔다. 이렇게 하면 핸들러의 start/pause/resume/stop/cleanup 코드가 백엔드 종류를
몰라도 되고, 세션은 항상 "현재 backend 하나"만 들고 있게 된다(4절 참고).

```ts
// src/main/services/streaming-backend.ts (신규)
interface StreamingBackend {
  readonly kind: 'aws' | 'pipecat';
  startStreaming(meetingId, onPartial, onFinal, onError): Promise<void> | void;
  addAudioChunk(chunk: Buffer): void;
  stopStreaming(): Promise<void>;
}
```

- 기존 `TranscribeService`는 이 인터페이스를 이미 구조적으로 만족(메서드 시그니처 동일).
  `kind: 'aws'` getter만 추가하거나 얇은 어댑터로 감싼다.
- **`PipecatBridgeService`** (`src/main/services/pipecat-bridge.service.ts`, 신규)가
  `kind: 'pipecat'`로 같은 인터페이스를 구현. 책임은 "Pipecat 서버와의 WS 통신":
  - `startStreaming` — `ws://localhost:8765` 연결 → `start` 메시지 송신 → `ready` ack 대기
    (기존 `waitForFirstChunk`와 동일한 가드).
  - `addAudioChunk` — `audio` 메시지로 송신(`seq` 증가).
  - `stopStreaming` — **drain/ack 종료**: `stop` 송신 → 서버가 마저 보내는
    `final`/`correction`을 계속 수신 → `stopped` ack(또는 타임아웃, 기본 ~3s) 수신 후에야
    WS close. ack 전에 소켓을 닫지 않아 in-flight tail 유실을 막는다. idempotent.
- 교정/번역은 Pipecat 경로에선 서버 파이프라인(LLM)이 담당하므로, main의
  `correctionService`/`translationService`는 **null**로 두고 사용하지 않는다. (correction
  결과는 WS `correction` 메시지로 들어와 IPC `TRANSCRIPTION_CORRECTED`로 전달된다.)
- **프로세스 라이프사이클 코드 없음** (spawn/SIGTERM/health-by-process 전부 제외).
- 연결 실패 시: 연결 불가하면 `onError`로
  "Pipecat 서버를 먼저 실행하세요 (`cd server && python bot.py`)" 안내 메시지 전파.
- 제한적 재연결: 1~2회 재시도 후 실패 시 에러 표면화. 재연결 시 이미 본 `resultId`를
  유지해 중복 final을 억제한다(3.4 idempotency).

### 3.3 Renderer View (`AgenticMeetingView.tsx`, 신규)

- 위치: `src/renderer/components/meeting-types/AgenticMeetingView.tsx`
- 기존 `meeting-types/*View.tsx` 패턴 그대로 따른다.
- 전사/교정 결과를 표시하고, **"Pipecat 파이프라인 활성/서버 미연결" 상태 배지**를
  노출해 데모 가시성을 높인다.
- `meeting-types/index.ts` 및 타입 매핑에 등록.

### 3.4 WebSocket 프로토콜 (main ↔ server)

JSON 메시지 기반. **버전 필드(`v`)를 포함**하고, zod 스키마로 양쪽 계약을 정의한다
(앱은 이미 zod 의존성 보유). 프로토콜은 단순 텍스트 전달이 아니라 **기존
`TranscriptionSegment` persistence 계약을 그대로 만족**시켜야 한다(아래 매핑 참고).

모든 메시지는 공통 필드를 갖는다: `{ v: 1, meetingId: string, ... }`.

- Main → Server:
  - `{ v, type: "start", meetingId, language, targetLanguage?, vocabularyName?,
    enableCorrection }` — 세션 시작 파라미터. 서버는 이 `meetingId`를 이후 모든 응답에
    echo한다.
  - `{ v, type: "audio", meetingId, seq: number, data: <base64 PCM Int16, 16kHz mono> }`
    — `seq`는 단조 증가하는 청크 시퀀스(누락/순서 디버깅용).
  - `{ v, type: "stop", meetingId }` — drain 요청. 서버는 in-flight STT/LLM을 모두 비우고
    남은 `final`/`correction`을 전송한 뒤 `stopped`를 보낸다(아래 종료 프로토콜).
- Server → Main:
  - `{ v, type: "ready", meetingId }` — 핸드셰크 ack (오디오 송신 시작 가드)
  - `{ v, type: "partial", meetingId, text, speakerLabel? }` — 비영속(화면 표시용).
  - `{ v, type: "final", meetingId, resultId, text, startTime, endTime, speakerLabel?,
    confidence? }` — **영속 대상**. 아래 매핑 규칙 적용.
  - `{ v, type: "correction", meetingId, resultId, original, corrected }` — `resultId`로
    원본 final 세그먼트와 상관.
  - `{ v, type: "stopped", meetingId }` — **종료 ack**. 서버가 drain을 마치고 더 보낼
    `final`/`correction`이 없음을 보장. main은 이 메시지(또는 타임아웃) 후에만 WS를 닫는다.
  - `{ v, type: "error", meetingId?, message }`

**세그먼트 식별 & persistence 매핑**: 현재 `TranscribeService`는 AWS가 주는 `ResultId`로
세그먼트를 만들고, main이 `id`(uuid)·`meetingId`·`createdAt`을 붙여 저장한다. Pipecat
경로도 이 계약을 동일하게 만족해야 하므로:

- **`resultId`의 출처**: Pipecat의 `AWSTranscribeSTTService`가 내보내는 전사 프레임에서
  안정적 식별자를 추출해 `resultId`로 사용한다. (Pipecat이 AWS `ResultId`를 직접 노출하지
  않으면, 서버가 `final` 프레임마다 `"{meetingId}:{monotonic_index}"` 형태의 결정적
  `resultId`를 부여한다. 구현 시 context7로 Pipecat STT 프레임의 식별자 노출 여부를
  재확인한다.)
- **`id` 부여**: main의 Bridge가 수신한 `final`을 `TranscriptionSegment`로 변환할 때
  기존 코드와 동일하게 `id = uuidv4()`, `meetingId`, `createdAt = new Date()`를 채운다.
  즉 DB row 식별자(`id`)는 여전히 main이 소유한다.
- **idempotency / 중복 억제 (DB 레벨 보강 필요)**: in-memory set만으로는 앱 재시작·재연결
  후 중복을 막지 못한다. 따라서 **DB 마이그레이션으로 `transcription_segments`에
  `(meeting_id, result_id)` UNIQUE 제약을 추가**하고, `saveSegment`를
  `INSERT ... ON CONFLICT(meeting_id, result_id) DO NOTHING`(또는 `DO UPDATE`로 텍스트
  갱신)으로 바꾼다. 이로써 재전송된 같은 final은 DB가 멱등하게 흡수한다. in-memory set은
  불필요한 DB 왕복을 줄이는 최적화로만 보조 사용한다. partial은 비영속이라 무시.
  - **현황 주의**: 현재 스키마는 `result_id TEXT NOT NULL`이지만 UNIQUE가 아니고,
    `saveSegment`는 단순 `INSERT`다. 따라서 이 마이그레이션은 **필수 작업**이다.
    AWS 경로도 동일 보강의 혜택을 받으므로 회귀 위험 낮음(같은 final 중복 insert 방지).
- **correction ↔ segment 연결 (기존 모델 정합)**: 현재 `corrected_sentences`는 `resultId`가
  아니라 **DB row `id` 배열(`segment_ids` JSON)** 로 세그먼트와 연결된다. Pipecat
  `correction` 메시지는 `resultId`만 들고 오므로, **main이 `(meeting_id, result_id)`로
  대상 세그먼트의 row `id`를 조회한 뒤** 기존 `saveCorrectedSentence`의 `segmentIds`
  계약을 그대로 채워 저장한다. 즉 `resultId`는 조회 키일 뿐, 영속 연결은 기존처럼 row `id`로
  유지한다(correction drift 방지).
  - 매칭되는 final이 아직 저장되지 않았으면(순서 역전) Bridge가 짧게 보류 후, 타임아웃 시
    폐기하고 로그를 남긴다(고아 correction 방지).
- **ordering**: `final`은 `startTime` 오름차순을 기대하나 네트워크 재정렬 가능성이 있으므로,
  persistence는 위 UNIQUE upsert로 처리하고 표시 정렬은 `startTime`을 따른다.

**직렬화 결정**: Pipecat 표준 `ProtobufFrameSerializer` 대신 **커스텀 JSON 직렬화**를
사용해 Electron 클라이언트를 단순화한다. Pipecat 측에서는 커스텀
`FrameSerializer`(또는 raw WS 핸들러)로 이 JSON ↔ Pipecat Frame 변환을 구현한다.
구현 시 context7로 Pipecat 커스텀 serializer 패턴을 재확인한다.

## 4. 모드 분기 & 라이프사이클

지적된 핵심 리스크는 "start만 새 경로로 바꾸고 pause/resume/stop은 옛 경로에 묶여 있어
세션이 잘못된 백엔드로 흐르거나 cleanup이 누락"되는 것이다. 이를 막기 위해 **세션이
백엔드 종류를 식별자로 들고**, 모든 제어 경로가 그 식별자를 보고 동작하게 한다.

### 4.1 세션 상태 계약 (`session-manager.service`)

`MeetingSessionState`를 다음과 같이 확장한다:

- `transcribeService: TranscribeService | null` 필드를 **`backend: StreamingBackend | null`**
  로 일반화한다(또는 기존 필드를 유지하되 `StreamingBackend`를 담을 수 있게 타입 확장).
  세션은 **항상 backend를 최대 1개만** 보유한다.
- `backendKind: 'aws' | 'pipecat'`를 세션에 저장해 제어 경로가 분기 없이 일관되게
  `session.backend`를 다루게 한다.
- `clearSession()`은 `backend.stopStreaming()`을 호출하므로(인터페이스 공통) AWS·Pipecat
  모두 동일하게 정리된다. Pipecat의 `stopStreaming`은 WS를 닫는다.

### 4.2 제어 경로별 동작 (start / pause / resume / stop / cleanup)

`src/main/ipc/meeting.handlers.ts`의 각 핸들러를 backend 추상화 위에서 동작하도록 조정한다.

- **start** (`startStreaming` 내부 분기): `meetingType === 'agentic'`이면
  `PipecatBridgeService`를, 아니면 기존 경로로 `TranscribeService`(+Bedrock)를 만들어
  `session.backend`에 넣는다. 그 외 분기는 없다.
- **audio** (`AUDIO_CHUNK` 핸들러): 기존처럼 `session.backend.addAudioChunk()` 호출.
  backend가 Bridge면 자동으로 WS로 나간다(분기 불필요).
- **pause** (`MEETING_PAUSE`): 현재 코드는 `meetingStreamingService.stopStreaming()`
  (= AWS 세션 전용)을 호출한다. 이를 **`session.backend.stopStreaming()`** 호출로 바꿔
  백엔드 무관하게 스트림을 멈춘다. Pipecat의 경우 `stopStreaming`이 **drain/ack를 거친 뒤**
  WS를 닫으므로(3.2), pause 직전까지의 in-flight `final`/`correction`이 보존되고
  pause 후 오디오가 계속 흐르는 일도 없다. AWS 경로는 로컬 버퍼/교정이 main에 남아 기존과
  동일하게 동작한다.
- **resume** (`MEETING_RESUME`): 일시정지된 세션의 `meetingType`/`backendKind`를 보고
  **동일한 백엔드**로 다시 `startStreaming`한다(agentic은 다시 Bridge로). 기존의
  세션 상태 복원(recentSentences/correctedCount/timeOffset 등)은 그대로 유지하되,
  Pipecat 경로에서는 본 `resultId` set도 복원해 중복 final을 억제한다.
- **stop** (`MEETING_STOP`): 현재 `session.transcribeService.stopStreaming()`을 호출한다.
  이를 `session.backend.stopStreaming()`으로 일반화한다. Pipecat은 drain/ack를 마친 뒤
  종료하므로, 회의 상태를 `completed`로 바꾸고 요약을 생성하기 **전에** 마지막 `final`/
  `correction`이 모두 persist됨을 보장한다(요약 입력 누락 방지).
- **cleanup**: `sessionManager.clearSession()`이 단일 진입점. 공통 인터페이스 덕분에
  Pipecat WS 종료가 누락되지 않는다. drain ack가 타임아웃되면 경고 로그 후 강제 close한다.

> 구현 시 위 5개 핸들러에서 `meetingStreamingService.stopStreaming()` /
> `session.transcribeService` 직접 참조를 모두 `session.backend` 경유로 치환한다.
> 이것이 라이프사이클 안전성의 핵심 변경이다.

### 4.3 서버 라이프사이클 (사용자 수동 실행)

- 사용자가 `server/`에서 venv 셋업 후 `python bot.py`로 직접 실행한다.
- Electron은 서버를 띄우지 않으며, 연결만 시도한다.
- agentic 모드를 한 번도 쓰지 않으면 Bridge는 아무 동작도 하지 않는다(다른 모드 무영향).

## 5. 에러 처리 & 보안 트레이드오프

1. **서버 미실행/연결 실패** → Bridge가 감지, `TRANSCRIPTION_ERROR`로 실행 안내 메시지 표시.
2. **WS 연결 끊김** → 1~2회 재연결 시도, 실패 시 에러 표면화. 재연결 시 본 `resultId` set
   유지로 중복 final 억제(3.4).
3. **AWS(Transcribe/Bedrock) 에러** → 서버가 `{type:"error"}`로 전달, main이 IPC로 그대로 전파.
4. **핸드셰크 가드** → 서버 `{type:"ready"}` 수신 전에는 오디오를 보내지 않는다
   (기존 `waitForFirstChunk` 가드와 동일한 취지).
5. **고아 correction** → 매칭 final이 없는 `correction`은 짧게 보류 후 폐기(3.4).
6. **drain ack 타임아웃** → `stop` 후 `stopped`가 기본 ~3s 내 안 오면 경고 로그를 남기고
   강제 WS close(영구 hang 방지). 이 경우 일부 tail 유실 가능성을 로그로 가시화한다.

### 5.1 보안 트레이드오프 (자격증명 별도 관리)

본 설계는 자격증명을 Pipecat 서버가 독립 관리한다(2절). 기존 앱은 자격증명을 암호화
저장(electron-store)하고 sensitive 처리를 main 프로세스에 가두는데, 이 설계는 그 신뢰
경계 밖에 **두 번째 자격증명 저장소(`server/.env` 평문)**를 만든다. 이는 의도된
트레이드오프이며, 다음 전제·완화책 하에 수용한다:

- **전제**: 로컬 데모 / 단일 개발자 머신. 서버는 `localhost`에만 바인딩하고 외부에
  노출하지 않는다.
- **완화책**: `server/.env`는 `.gitignore`에 포함(커밋 금지). 서버 로그에 자격증명을
  남기지 않는다. README에 "장기 키 대신 단기 STS 토큰/AWS 프로파일 사용 권장"을 명시.
- **후속 과제**: 프로덕션 전환 시 자격증명을 main이 소유하고 단기 토큰을 핸드셰크로
  주입하는 방식으로 trust boundary를 일원화한다(10절).

## 6. 배포 / 실행

데모 제출 기준이므로 **개발 실행 + 사용자 수동 서버 실행** 우선:

- 앱: 기존 그대로 `npm start`.
- 서버: `cd server && python -m venv .venv && source .venv/bin/activate &&
  pip install -r requirements.txt`, 이후 `cp .env.example .env`(자격증명 입력) →
  `python bot.py`.
- 프로덕션 번들링(PyInstaller 등)은 **YAGNI** — 후속 과제로만 기록.

## 7. 테스트 전략

- **Bridge 단위 테스트** (vitest): `PipecatBridgeService`의 WS 프로토콜 인코딩/디코딩,
  콜백 매핑, 재연결 로직을 mock WebSocket으로 검증. 기존 `transcribe.service.test.ts`
  패턴을 따른다.
- **프로토콜 계약 테스트**: main↔server JSON 메시지 스키마를 zod로 정의하고 직렬화 검증.
  버전 필드(`v`)·`meetingId`·`resultId` 필수 여부 포함.
- **persistence/idempotency 테스트**: WS `final` → `TranscriptionSegment` 매핑(id/meetingId/
  createdAt 부여), **`(meeting_id, result_id)` UNIQUE 제약 + ON CONFLICT로 같은 final이
  두 번 들어와도 row가 하나만 생기는지**(앱 재시작 시나리오 포함), `correction`이
  `(meeting_id, result_id)`로 대상 row `id`를 찾아 기존 `segmentIds` 계약으로 저장되는지,
  고아 correction 폐기를 검증.
- **DB 마이그레이션 테스트**: 기존 데이터가 있는 DB에 UNIQUE 마이그레이션 적용 시 중복
  `result_id` 행이 있으면 어떻게 처리되는지(사전 정리 또는 partial index) 검증.
- **라이프사이클 테스트**: `StreamingBackend` 추상화 위에서 start→pause→resume→stop이
  동일 backend로 일관되게 흐르는지, pause 후 `addAudioChunk`가 서버로 안 나가는지,
  `clearSession`이 Pipecat WS를 닫는지 mock backend로 검증.
- **drain/tail-loss 테스트**: `stop` 후 서버가 늦게 보낸 `final`/`correction`이
  `stopped` ack 전에 도착하면 persist되고, ack 후에야 WS가 닫히는지, ack 타임아웃 시
  강제 close + 경고 로그가 남는지 검증. stop race(재연결 중 stop)도 포함.
- **Python bot**: 데모 범위에선 수동 스모크 검증(짧은 PCM 픽스처로 STT 1회 통과 확인).
  무거운 자동화는 제외.
- **회귀**: 기존 4개 모드(AWS backend)의 start/pause/resume/stop이 추상화 도입 후에도
  동일하게 동작하는지 `meeting.handlers` 관련 기존 테스트 통과 확인.

## 8. 타입/상수 변경

- `src/shared/types/meeting.ts`:
  - `MeetingType` union에 `'agentic'` 추가
  - `MEETING_TYPES` 배열에 카드 1개 추가 (icon: `smart_toy` 또는
    `network_intelligence`, indigo/amber 계열)
  - `MeetingMetadataMap`에 `agentic` 항목 추가 (초기엔 빈 메타 또는 최소 필드)
- `src/renderer/components/meeting-types/`: `AgenticMeetingView.tsx` 추가 + `index.ts` 등록
- 신규 IPC 채널이 필요하면 `ipc-channels.ts`에 추가(기존 `TRANSCRIPTION_*` 재사용 우선)

## 9. 영향받는 파일 (예상)

신규:
- `server/bot.py`, `server/requirements.txt`, `server/.env.example`, `server/README.md`,
  `server/.gitignore`(`.env` 제외)
- `src/main/services/streaming-backend.ts` (공통 인터페이스 + AWS 어댑터)
- `src/main/services/pipecat-bridge.service.ts`
- `src/main/services/__tests__/pipecat-bridge.service.test.ts`
- `src/renderer/components/meeting-types/AgenticMeetingView.tsx`

수정:
- `src/main/ipc/meeting.handlers.ts` (start 분기 + pause/resume/stop을 `session.backend`로 일반화)
- `src/main/services/session-manager.service.ts` (`backend`/`backendKind` 필드, clearSession)
- `src/main/services/meeting-streaming.service.ts` (AWS 경로를 backend로 래핑)
- `src/main/migrations/index.ts` (`transcription_segments`에 `(meeting_id, result_id)`
  UNIQUE 제약 추가 마이그레이션)
- `src/main/services/database.service.ts` (`saveSegment`를 ON CONFLICT upsert로,
  `correction` → row `id` 조회 헬퍼 추가)
- `src/shared/types/meeting.ts` (MeetingType / MEETING_TYPES / MetadataMap)
- `src/renderer/components/meeting-types/index.ts`
- `src/shared/constants/ipc-channels.ts` (기존 `TRANSCRIPTION_*` 재사용 우선, 필요 시만 추가)

## 10. 후속 과제 (이번 범위 밖)

- agentic 능동 동작: 실시간 질문/액션아이템 추출, MCP/tool-calling, 음성 Q&A, 실시간 요약
- Pipecat 서버 프로세스 번들링(PyInstaller) 및 자동 spawn
- WebRTC/Daily transport 등 Pipecat 표준 transport로의 확장
- **trust boundary 일원화**: 자격증명을 main이 소유하고 단기 STS 토큰을 WS 핸드셰크로
  주입하여 `server/.env` 평문 키 의존을 제거(5.1 완화책의 프로덕션화).
