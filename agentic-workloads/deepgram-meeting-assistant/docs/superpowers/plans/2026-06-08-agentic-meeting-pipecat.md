# Agentic Meeting (Pipecat) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 Electron 앱에 `agentic` 회의 모드를 추가한다. 이 모드는 AWS SDK 직접 호출 대신, 사용자가 직접 실행하는 로컬 Python Pipecat 서버(`server/`)에 WebSocket으로 연결해 AWS Transcribe STT + Bedrock LLM 파이프라인을 구동한다.

**Architecture:** 두 스트리밍 경로(AWS 직접 / Pipecat)를 공통 `StreamingBackend` 인터페이스 뒤에 둔다. 세션은 항상 backend 하나만 보유하고, start/pause/resume/stop/cleanup이 모두 `session.backend`를 통해 동작한다. Pipecat 경로는 `PipecatBridgeService`(WS 클라이언트)가 담당하며, drain/ack 종료 프로토콜과 `(meeting_id, result_id)` DB UNIQUE 제약으로 전사/교정 데이터 정합성을 보장한다.

**Tech Stack:** Electron 39 + TypeScript 5 (strict), React 19, better-sqlite3, zod, vitest. 신규: `ws`(Node WebSocket 클라이언트), Python 3 + pipecat-ai[aws] + FastAPI/uvicorn(서버).

**Spec:** `docs/superpowers/specs/2026-06-08-agentic-meeting-pipecat-design.md`

---

## File Structure

신규 파일:
- `src/shared/types/pipecat-protocol.ts` — main↔server WS JSON 메시지 zod 스키마 + 타입
- `src/main/services/streaming-backend.ts` — `StreamingBackend` 인터페이스 + AWS 어댑터
- `src/main/services/pipecat-bridge.service.ts` — Pipecat WS 클라이언트 backend
- `src/main/services/__tests__/pipecat-bridge.service.test.ts`
- `src/renderer/components/meeting-types/AgenticMeetingView.tsx`
- `server/bot.py`, `server/requirements.txt`, `server/.env.example`, `server/README.md`, `server/.gitignore`

수정 파일:
- `src/shared/types/meeting.ts` — `MeetingType`에 `'agentic'`, `MEETING_TYPES` 카드, `MeetingMetadataMap`
- `src/main/ipc/meeting.handlers.ts` — `MeetingCreateSchema` enum, start 분기, AUDIO_CHUNK, pause/resume/stop을 backend 경유로
- `src/main/services/session-manager.service.ts` — `backend`/`backendKind` 필드, clearSession
- `src/main/migrations/index.ts` — `(meeting_id, result_id)` UNIQUE 마이그레이션
- `src/main/services/database.service.ts` — `saveSegment` ON CONFLICT, `getSegmentRowIdsByResultId` 헬퍼
- `src/renderer/components/meeting-types/index.ts` — `AgenticMeetingView` export
- `src/renderer/components/MeetingView.tsx` — `case 'agentic'` 라우팅 + `MeetingViewProps.onEndRecording` 반환 타입 변경
- `src/preload/preload.ts` — `stopMeeting` 반환 타입에 `degraded?: boolean`
- `src/renderer/hooks/useMeeting.ts` — `stopMeeting`이 `{ completed, degraded }` 반환
- `src/renderer/hooks/useAppState.ts` — `handleEndRecording`이 stop 결과 전달
- `src/renderer/hooks/useRecordingControls.ts` — `handleEnd`가 degraded면 자동 요약(`onRecordingComplete`) 차단

---

## Task 1: MeetingType에 'agentic' 추가 (타입/카드/스키마)

**Files:**
- Modify: `src/shared/types/meeting.ts`
- Modify: `src/main/ipc/meeting.handlers.ts:41-45` (MeetingCreateSchema)

- [ ] **Step 1: `MeetingType` union에 `'agentic'` 추가**

`src/shared/types/meeting.ts` 상단의 union 수정:

```ts
export type MeetingType =
  | 'interview'
  | 'english'
  | 'translated'
  | 'client'
  | 'weekly'
  | 'agentic';
```

- [ ] **Step 2: `MEETING_TYPES` 배열에 카드 추가**

같은 파일의 `MEETING_TYPES` 배열 끝(interview 항목 뒤)에 추가:

```ts
  {
    id: 'agentic',
    label: 'Agentic Meeting',
    description: 'Pipecat pipeline: real-time STT + LLM via local server',
    bgColor: 'bg-indigo-50',
    textColor: 'text-indigo-600',
    icon: 'smart_toy',
  },
```

- [ ] **Step 3: `MeetingMetadataMap`에 `agentic` 항목 추가**

같은 파일의 `MeetingMetadataMap` 타입에 추가 (빈 메타로 시작):

```ts
export type MeetingMetadataMap = {
  client: ClientMeetingMetadata;
  interview: InterviewMeetingMetadata;
  english: TranslatedMeetingMetadata;
  translated: TranslatedMeetingMetadata;
  weekly: WeeklyMeetingMetadata;
  agentic: Record<string, never>;
};
```

- [ ] **Step 4: `MeetingCreateSchema` enum에 `'agentic'` 추가**

`src/main/ipc/meeting.handlers.ts`의 `MeetingCreateSchema` (line 41-45):

```ts
const MeetingCreateSchema = z.object({
  type: z.enum(['client', 'weekly', 'english', 'translated', 'interview', 'agentic']),
  language: z.enum(['ko-KR', 'en-US', 'ja-JP', 'zh-CN']),
  title: z.string().optional(),
});
```

- [ ] **Step 5: 타입체크 통과 확인**

Run: `npm run build`
Expected: 컴파일 에러 없음 (PASS). 만약 `MeetingView.tsx`의 switch에서 `agentic` 미처리 경고가 나면 Task 10에서 처리하므로 이 단계에선 `default` 케이스가 있어 빌드는 통과한다.

- [ ] **Step 6: Commit**

```bash
git add src/shared/types/meeting.ts src/main/ipc/meeting.handlers.ts
git commit -m "feat(agentic): add 'agentic' meeting type, card, and create schema"
```

---

## Task 2: DB 마이그레이션 — (meeting_id, result_id) UNIQUE + saveSegment 멱등화

> **테스트 전략 (중요):** 기존 `database.service.test.ts`는 `better-sqlite3`를 **완전히 mock**하고(`DatabaseService` 생성자는 zero-arg, `app.getPath`로 db 경로 결정), 실제 SQL을 실행하지 않는다. 따라서 UNIQUE 인덱스 / `ON CONFLICT` / 마이그레이션 cleanup 같은 **실제 SQL 동작은 그 파일로 검증 불가**하다. 이 태스크는 **실제 `better-sqlite3` in-memory DB**를 직접 쓰는 새 통합 테스트 파일을 만들어 마이그레이션과 saveSegment SQL을 검증한다. (`better-sqlite3`는 이미 dependency이고 `:memory:`를 지원한다.)

**Files:**
- Create: `src/main/migrations/__tests__/migrations.integration.test.ts` (실제 SQLite)
- Modify: `src/main/migrations/index.ts` (migrations 배열 끝에 version 8 추가)
- Modify: `src/main/services/database.service.ts:255-272` (saveSegment)

- [ ] **Step 1: 실제 SQLite 통합 테스트 작성 (실패)**

`src/main/migrations/__tests__/migrations.integration.test.ts`. 이 파일은 `better-sqlite3`를 mock하지 **않는다**(파일 상단에 `vi.mock('better-sqlite3')` 없음). 실제 in-memory DB에 `runMigrations`를 돌리고, version 1 스키마의 `transcription_segments`에 직접 INSERT한 뒤 UNIQUE 제약과 ON CONFLICT 동작을 검증한다.

```ts
import { describe, it, expect, beforeEach } from 'vitest';
import Database from 'better-sqlite3';
import { runMigrations } from '../index';

// saveSegment와 동일한 멱등 SQL (Task 2 Step 4에서 database.service.ts에 반영하는 것과 일치해야 함)
const SAVE_SEGMENT_SQL = `
  INSERT INTO transcription_segments
  (id, meeting_id, result_id, text, start_time, end_time, speaker_label, confidence, created_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  ON CONFLICT(meeting_id, result_id) DO UPDATE SET
    text = excluded.text,
    start_time = excluded.start_time,
    end_time = excluded.end_time,
    speaker_label = excluded.speaker_label,
    confidence = excluded.confidence
`;

describe('migrations + segment idempotency (real sqlite)', () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(':memory:');
    db.pragma('foreign_keys = ON');
    runMigrations(db);
    // 부모 meeting 생성 (FK 충족)
    db.prepare(`
      INSERT INTO meetings (id, type, title, status, language, started_at, duration, created_at, updated_at)
      VALUES ('m1', 'agentic', 't', 'recording', 'ko-KR', '2026-01-01', 0, '2026-01-01', '2026-01-01')
    `).run();
  });

  it('creates a UNIQUE index on (meeting_id, result_id)', () => {
    const idx = db.prepare(`SELECT name FROM sqlite_master WHERE type='index' AND name='idx_segments_meeting_result'`).get();
    expect(idx).toBeDefined();
  });

  it('keeps only one row when same (meeting_id, result_id) inserted twice via ON CONFLICT', () => {
    const ins = db.prepare(SAVE_SEGMENT_SQL);
    ins.run('row-1', 'm1', 'r-1', 'hello', 0, 1, null, null, '2026-01-01');
    ins.run('row-2', 'm1', 'r-1', 'hello world', 0, 1.5, null, null, '2026-01-01');
    const rows = db.prepare(`SELECT * FROM transcription_segments WHERE meeting_id='m1'`).all() as any[];
    expect(rows).toHaveLength(1);
    expect(rows[0].text).toBe('hello world'); // DO UPDATE로 갱신됨
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `npm test -- migrations.integration`
Expected: FAIL — version 8 마이그레이션이 없어 UNIQUE 인덱스가 생성되지 않고, ON CONFLICT 절의 대상 제약이 없어 두 row가 모두 삽입됨.

- [ ] **Step 3: 마이그레이션 version 8 추가**

`src/main/migrations/index.ts`의 `migrations` 배열 끝(version 7 뒤, 닫는 `]` 앞)에 추가:

```ts
  {
    version: 8,
    name: 'add_unique_meeting_result_to_segments',
    up: (db) => {
      // 기존 중복 (meeting_id, result_id) 행이 있으면 가장 먼저 들어온 것만 남기고 정리
      db.exec(`
        DELETE FROM transcription_segments
        WHERE rowid NOT IN (
          SELECT MIN(rowid) FROM transcription_segments
          GROUP BY meeting_id, result_id
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_segments_meeting_result
          ON transcription_segments(meeting_id, result_id);
      `);
    },
  },
```

- [ ] **Step 4: `saveSegment`를 ON CONFLICT 멱등 INSERT로 변경**

`src/main/services/database.service.ts:255-272`:

```ts
  saveSegment(segment: Omit<TranscriptionSegment, 'createdAt'>): void {
    const stmt = this.db.prepare(`
      INSERT INTO transcription_segments
      (id, meeting_id, result_id, text, start_time, end_time, speaker_label, confidence, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(meeting_id, result_id) DO UPDATE SET
        text = excluded.text,
        start_time = excluded.start_time,
        end_time = excluded.end_time,
        speaker_label = excluded.speaker_label,
        confidence = excluded.confidence
    `);
    stmt.run(
      segment.id,
      segment.meetingId,
      segment.resultId,
      segment.text,
      segment.startTime,
      segment.endTime,
      segment.speakerLabel,
      segment.confidence || null,
      new Date().toISOString()
    );
  }
```

> 동작: 같은 `(meeting_id, result_id)`가 다시 오면 새 row를 만들지 않고 기존 row의 텍스트/타임을 갱신한다. AWS 경로도 동일하게 중복 final로부터 보호된다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `npm test -- migrations.integration`
Expected: PASS (UNIQUE 인덱스 생성 + ON CONFLICT 갱신)

추가로 기존 mock 기반 테스트가 깨지지 않았는지 확인:
Run: `npm test -- database.service`
Expected: PASS (기존 테스트는 mock이라 SQL 변경 영향 없음 — `saveSegment`가 `prepare`에 넘기는 SQL 문자열만 바뀌므로, 해당 테스트가 `stringContaining('INSERT INTO transcription_segments')`로 검사한다면 여전히 통과. 만약 정확한 SQL 문자열을 단언하는 테스트가 있으면 그 단언을 새 SQL에 맞게 갱신한다.)

- [ ] **Step 6: Commit**

```bash
git add src/main/migrations/index.ts src/main/migrations/__tests__/migrations.integration.test.ts src/main/services/database.service.ts
git commit -m "feat(db): enforce (meeting_id, result_id) uniqueness with idempotent saveSegment"
```

---

## Task 3: correction → segment row id 조회 헬퍼

> Task 2와 동일한 이유로 실제 SQLite 통합 테스트(`migrations.integration.test.ts`)에 케이스를 추가해 검증한다. `getSegmentRowIdsByResultId`는 `DatabaseService` 메서드지만, 실제 SQL 동작 검증이 핵심이므로 통합 테스트에서 동일 SQL을 직접 실행해 단언한다(헬퍼는 그 SQL을 그대로 감싼다).

**Files:**
- Modify: `src/main/services/database.service.ts` (getSegmentsByMeeting 뒤에 메서드 추가)
- Modify: `src/main/migrations/__tests__/migrations.integration.test.ts` (조회 SQL 케이스 추가)

- [ ] **Step 1: 조회 SQL 실패 테스트 작성**

`migrations.integration.test.ts`의 `describe` 블록 안에 케이스 추가. `getSegmentRowIdsByResultId`가 실행하는 것과 동일한 SQL을 실제 DB에 돌려 단언한다:

```ts
const ROW_IDS_BY_RESULT_SQL = `SELECT id FROM transcription_segments WHERE meeting_id = ? AND result_id = ?`;

it('returns row id(s) for a given (meeting_id, result_id)', () => {
  db.prepare(SAVE_SEGMENT_SQL).run('row-abc', 'm1', 'r-1', 'hi', 0, 1, null, null, '2026-01-01');
  const rows = db.prepare(ROW_IDS_BY_RESULT_SQL).all('m1', 'r-1') as Array<{ id: string }>;
  expect(rows.map((r) => r.id)).toEqual(['row-abc']);
});

it('returns empty when no segment matches', () => {
  const rows = db.prepare(ROW_IDS_BY_RESULT_SQL).all('m1', 'nope') as Array<{ id: string }>;
  expect(rows).toHaveLength(0);
});
```

- [ ] **Step 2: 테스트 실패 확인**

> 이 케이스들은 SQL만 검증하므로 Step 1 작성 직후엔 실제로 통과할 수도 있다(SQL 자체는 유효). 목적은 `database.service.ts`에 헬퍼 메서드를 추가하기 위한 계약 고정이다. 만약 통과하면 그대로 두고 Step 3으로 진행한다.

Run: `npm test -- migrations.integration`
Expected: PASS 또는 (오타 시) FAIL

- [ ] **Step 3: 헬퍼 메서드 구현**

`src/main/services/database.service.ts`의 `getSegmentsByMeeting` 메서드 바로 뒤에 추가:

```ts
  getSegmentRowIdsByResultId(meetingId: string, resultId: string): string[] {
    const stmt = this.db.prepare(`
      SELECT id FROM transcription_segments
      WHERE meeting_id = ? AND result_id = ?
    `);
    const rows = stmt.all(meetingId, resultId) as Array<{ id: string }>;
    return rows.map((r) => r.id);
  }
```

- [ ] **Step 4: 빌드 + 테스트 통과 확인**

Run: `npm run build && npm test -- migrations.integration`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main/services/database.service.ts src/main/migrations/__tests__/migrations.integration.test.ts
git commit -m "feat(db): add getSegmentRowIdsByResultId for correction mapping"
```

---

## Task 4: StreamingBackend 인터페이스 + AWS 어댑터

**Files:**
- Create: `src/main/services/streaming-backend.ts`
- Modify: `src/main/services/transcribe.service.ts` (kind getter 추가)

- [ ] **Step 1: 인터페이스 정의 파일 생성**

`src/main/services/streaming-backend.ts`:

```ts
/**
 * Streaming Backend 공통 인터페이스
 *
 * AWS 직접 경로(TranscribeService)와 Pipecat 경로(PipecatBridgeService)를
 * 하나의 인터페이스 뒤에 둬서, 핸들러의 start/pause/resume/stop/cleanup이
 * 백엔드 종류를 몰라도 동작하게 한다.
 */
import type { TranscriptionSegment } from '../../shared/types/transcription';

export type StreamingBackendKind = 'aws' | 'pipecat';

export interface StreamingBackend {
  readonly kind: StreamingBackendKind;
  startStreaming(
    meetingId: string,
    onPartialResult: (text: string, speakerLabel: string | null) => void,
    onFinalResult: (segment: TranscriptionSegment) => void | Promise<void>,
    onError: (error: Error) => void
  ): Promise<void> | void;
  addAudioChunk(chunk: Buffer): void;
  stopStreaming(): Promise<void>;
}
```

- [ ] **Step 2: `TranscribeService`에 `kind` getter 추가**

`src/main/services/transcribe.service.ts`의 `TranscribeService` 클래스 본문 상단(생성자 위 또는 아래)에 추가하고, `StreamingBackend`를 implements 한다. 클래스 선언과 멤버를 다음과 같이 수정:

```ts
import type { StreamingBackend, StreamingBackendKind } from './streaming-backend';

export class TranscribeService implements StreamingBackend {
  readonly kind: StreamingBackendKind = 'aws';
  // ...기존 멤버 그대로...
```

> `TranscribeService`는 이미 `startStreaming`/`addAudioChunk`/`stopStreaming` 시그니처가 인터페이스와 일치하므로 추가 변경은 `kind` 뿐이다.

- [ ] **Step 3: 타입체크 통과 확인**

Run: `npm run build`
Expected: PASS — `TranscribeService`가 `StreamingBackend`를 만족.

- [ ] **Step 4: Commit**

```bash
git add src/main/services/streaming-backend.ts src/main/services/transcribe.service.ts
git commit -m "feat(streaming): add StreamingBackend interface and mark TranscribeService as aws backend"
```

---

## Task 5: SessionManager에 backend 추상화 반영

**Files:**
- Modify: `src/main/services/session-manager.service.ts`
- Test: `src/main/services/__tests__/session-manager.service.test.ts`

- [ ] **Step 1: backend 필드 실패 테스트 작성**

`session-manager.service.test.ts`에 추가:

```ts
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { sessionManager } from '../session-manager.service';

describe('session backend abstraction', () => {
  beforeEach(() => {
    sessionManager.resetSession();
  });

  it('stores a backend and its kind on the session', () => {
    const fakeBackend: any = { kind: 'pipecat', stopStreaming: vi.fn() };
    sessionManager.createSession({
      meetingId: 'm1',
      meetingType: 'agentic',
      language: 'ko-KR',
      backend: fakeBackend,
      backendKind: 'pipecat',
    });
    const session = sessionManager.getSession();
    expect(session?.backend).toBe(fakeBackend);
    expect(session?.backendKind).toBe('pipecat');
  });

  it('clearSession calls backend.stopStreaming', async () => {
    const stop = vi.fn().mockResolvedValue(undefined);
    const fakeBackend: any = { kind: 'aws', stopStreaming: stop };
    sessionManager.createSession({
      meetingId: 'm1', meetingType: 'weekly', language: 'ko-KR',
      backend: fakeBackend, backendKind: 'aws',
    });
    await sessionManager.clearSession();
    expect(stop).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `npm test -- session-manager`
Expected: FAIL — `backend`/`backendKind`가 CreateSessionParams/세션에 없음.

- [ ] **Step 3: `MeetingSessionState`와 `CreateSessionParams`에 backend 필드 추가**

`src/main/services/session-manager.service.ts`:

import 추가 (상단):

```ts
import type { StreamingBackend, StreamingBackendKind } from './streaming-backend';
```

`MeetingSessionState`에 필드 추가(기존 `transcribeService` 필드는 하위호환 위해 유지하되, 신규 코드는 `backend`를 사용):

```ts
export interface MeetingSessionState {
  meetingId: string;
  meetingType: MeetingType;
  language: TranscribeLanguage;
  targetLanguage: TranscribeLanguage;
  backend: StreamingBackend | null;
  backendKind: StreamingBackendKind | null;
  transcribeService: TranscribeService | null; // @deprecated AWS 경로 하위호환
  correctionService: BedrockService | null;
  translationService: BedrockService | null;
  sentenceBuffer: SentenceBufferService;
  recentSentences: string[];
  correctedCount: number;
  titleGenerated: boolean;
  prepData: MeetingPrepData | null;
  transcribeTimeOffsetSec: number;
  lastSegmentEndTimeSec: number;
}
```

`CreateSessionParams`에 추가:

```ts
export interface CreateSessionParams {
  meetingId: string;
  meetingType: MeetingType;
  language: TranscribeLanguage;
  targetLanguage?: TranscribeLanguage;
  backend?: StreamingBackend | null;
  backendKind?: StreamingBackendKind | null;
  transcribeService?: TranscribeService | null;
  correctionService?: BedrockService | null;
  translationService?: BedrockService | null;
  sentenceBuffer?: SentenceBufferService;
  prepData?: MeetingPrepData | null;
}
```

- [ ] **Step 4: `createSession`에서 backend 초기화**

`createSession` 본문의 객체 리터럴에 추가(`transcribeService` 줄 위):

```ts
    this.session = {
      meetingId: params.meetingId,
      meetingType: params.meetingType,
      language: params.language,
      targetLanguage: params.targetLanguage ?? 'ko-KR',
      backend: params.backend ?? params.transcribeService ?? null,
      backendKind: params.backendKind ?? (params.transcribeService ? 'aws' : null),
      transcribeService: params.transcribeService ?? null,
      correctionService: params.correctionService ?? null,
      translationService: params.translationService ?? null,
      sentenceBuffer: params.sentenceBuffer ?? new SentenceBufferService(params.language),
      recentSentences: [],
      correctedCount: 0,
      titleGenerated: false,
      prepData: params.prepData ?? null,
      transcribeTimeOffsetSec: 0,
      lastSegmentEndTimeSec: 0,
    };
```

- [ ] **Step 5: `clearSession`을 backend 경유로 변경**

`clearSession` (line 220-225):

```ts
  async clearSession(): Promise<void> {
    if (this.session?.backend) {
      // cleanup 경로: stopStreaming이 degraded로 reject해도 세션 정리는 진행한다.
      try {
        await this.session.backend.stopStreaming();
      } catch {
        // 정리 중 종료 실패는 무시(이미 영속된 데이터는 보존됨).
      }
    }
    this.session = null;
  }
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `npm test -- session-manager`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/main/services/session-manager.service.ts src/main/services/__tests__/session-manager.service.test.ts
git commit -m "feat(session): add backend/backendKind and route clearSession through backend"
```

---

## Task 6: WS 프로토콜 zod 스키마 (shared)

**Files:**
- Create: `src/shared/types/pipecat-protocol.ts`
- Test: `src/shared/types/__tests__/pipecat-protocol.test.ts`

- [ ] **Step 1: 프로토콜 스키마 실패 테스트 작성**

`src/shared/types/__tests__/pipecat-protocol.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { ServerMessageSchema, PROTOCOL_VERSION } from '../pipecat-protocol';

describe('pipecat protocol', () => {
  it('parses a valid final message', () => {
    const msg = {
      v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1',
      text: 'hello', startTime: 0, endTime: 1.2,
    };
    const parsed = ServerMessageSchema.parse(msg);
    expect(parsed.type).toBe('final');
  });

  it('rejects a final message without resultId', () => {
    const msg = { v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', text: 'x', startTime: 0, endTime: 1 };
    expect(() => ServerMessageSchema.parse(msg)).toThrow();
  });

  it('parses a stopped ack', () => {
    const parsed = ServerMessageSchema.parse({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' });
    expect(parsed.type).toBe('stopped');
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `npm test -- pipecat-protocol`
Expected: FAIL — 모듈 없음.

- [ ] **Step 3: 프로토콜 스키마 구현**

`src/shared/types/pipecat-protocol.ts`:

```ts
import { z } from 'zod';

export const PROTOCOL_VERSION = 1 as const;

const base = { v: z.literal(PROTOCOL_VERSION), meetingId: z.string() };

// Main → Server
export const ClientStartSchema = z.object({
  ...base,
  type: z.literal('start'),
  language: z.string(),
  targetLanguage: z.string().optional(),
  vocabularyName: z.string().optional(),
  enableCorrection: z.boolean(),
});
export const ClientAudioSchema = z.object({
  ...base, type: z.literal('audio'), seq: z.number().int(), data: z.string(),
});
export const ClientStopSchema = z.object({ ...base, type: z.literal('stop') });

export const ClientMessageSchema = z.discriminatedUnion('type', [
  ClientStartSchema, ClientAudioSchema, ClientStopSchema,
]);

// Server → Main
export const ServerReadySchema = z.object({ ...base, type: z.literal('ready') });
export const ServerPartialSchema = z.object({
  ...base, type: z.literal('partial'), text: z.string(), speakerLabel: z.string().nullish(),
});
export const ServerFinalSchema = z.object({
  ...base, type: z.literal('final'), resultId: z.string(), text: z.string(),
  startTime: z.number(), endTime: z.number(),
  speakerLabel: z.string().nullish(), confidence: z.number().nullish(),
});
export const ServerCorrectionSchema = z.object({
  ...base, type: z.literal('correction'), resultId: z.string(),
  original: z.string(), corrected: z.string(),
});
export const ServerStoppedSchema = z.object({ ...base, type: z.literal('stopped') });
export const ServerErrorSchema = z.object({
  v: z.literal(PROTOCOL_VERSION), type: z.literal('error'),
  meetingId: z.string().optional(), message: z.string(),
});

export const ServerMessageSchema = z.discriminatedUnion('type', [
  ServerReadySchema, ServerPartialSchema, ServerFinalSchema,
  ServerCorrectionSchema, ServerStoppedSchema, ServerErrorSchema,
]);

export type ClientMessage = z.infer<typeof ClientMessageSchema>;
export type ServerMessage = z.infer<typeof ServerMessageSchema>;
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `npm test -- pipecat-protocol`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shared/types/pipecat-protocol.ts src/shared/types/__tests__/pipecat-protocol.test.ts
git commit -m "feat(protocol): add versioned zod schema for pipecat WS messages"
```

---

## Task 7: PipecatBridgeService (WS 클라이언트 backend)

**Files:**
- Modify: `package.json` (add `ws`, `@types/ws`)
- Create: `src/main/services/pipecat-bridge.service.ts`
- Test: `src/main/services/__tests__/pipecat-bridge.service.test.ts`

- [ ] **Step 1: `ws` 의존성 설치**

Run:
```bash
npm install ws && npm install -D @types/ws
```
Expected: `package.json` dependencies에 `ws`, devDependencies에 `@types/ws` 추가.

- [ ] **Step 2: Bridge 동작 실패 테스트 작성**

`src/main/services/__tests__/pipecat-bridge.service.test.ts`. `ws`를 mock해서 가짜 소켓을 주입한다:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EventEmitter } from 'events';
import { PROTOCOL_VERSION } from '../../../shared/types/pipecat-protocol';

// ws mock: 생성된 인스턴스를 테스트에서 잡을 수 있게 전역에 보관
const sockets: any[] = [];
vi.mock('ws', () => {
  class FakeWS extends EventEmitter {
    static OPEN = 1;
    readyState = 1;
    sent: string[] = [];
    constructor() { super(); sockets.push(this); }
    send(data: string) { this.sent.push(data); }
    close() { this.emit('close'); }
  }
  return { default: FakeWS, WebSocket: FakeWS };
});

import { PipecatBridgeService } from '../pipecat-bridge.service';

function lastSocket() { return sockets[sockets.length - 1]; }

describe('PipecatBridgeService', () => {
  beforeEach(() => { sockets.length = 0; });

  it('has kind "pipecat"', () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    expect(bridge.kind).toBe('pipecat');
  });

  it('sends start message and resolves startStreaming after ready', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const onPartial = vi.fn(), onFinal = vi.fn(), onError = vi.fn();
    const p = bridge.startStreaming('m1', onPartial, onFinal, onError);
    const ws = lastSocket();
    ws.emit('open');
    // start 메시지가 나갔는지
    const startMsg = JSON.parse(ws.sent[0]);
    expect(startMsg.type).toBe('start');
    expect(startMsg.meetingId).toBe('m1');
    // ready 수신 → resolve
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
  });

  it('maps a final message to a TranscriptionSegment via onFinalResult', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const onFinal = vi.fn();
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('message', JSON.stringify({
      v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1',
      text: 'hello', startTime: 0, endTime: 1,
    }));
    expect(onFinal).toHaveBeenCalledOnce();
    const seg = onFinal.mock.calls[0][0];
    expect(seg.resultId).toBe('r1');
    expect(seg.meetingId).toBe('m1');
    expect(typeof seg.id).toBe('string'); // main이 uuid 부여
  });

  it('drops duplicate finals with the same resultId', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const onFinal = vi.fn();
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    const final = { v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 };
    ws.emit('message', JSON.stringify(final));
    ws.emit('message', JSON.stringify(final));
    expect(onFinal).toHaveBeenCalledOnce();
  });

  it('waits for stopped ack before closing on stopStreaming', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    const closeSpy = vi.spyOn(ws, 'close');
    const stopP = bridge.stopStreaming();
    // stop 메시지 전송 확인
    expect(JSON.parse(ws.sent[ws.sent.length - 1]).type).toBe('stop');
    // 아직 close 안 됨
    expect(closeSpy).not.toHaveBeenCalled();
    // stopped ack 수신 → close
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    await stopP;
    expect(closeSpy).toHaveBeenCalled();
  });

  it('runs correction AFTER the matching final persistence resolves (ordering)', async () => {
    const order: string[] = [];
    let resolveFinal: () => void = () => {};
    const onFinal = vi.fn().mockImplementation(() =>
      new Promise<void>((res) => { resolveFinal = () => { order.push('final'); res(); }; })
    );
    const onCorrection = vi.fn().mockImplementation(() => { order.push('correction'); });
    const bridge = new PipecatBridgeService({
      url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true, onCorrection,
    });
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    // correction이 final보다 먼저 도착(역전)
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'correction', meetingId: 'm1', resultId: 'r1', original: 'x', corrected: 'X' }));
    // 아직 final persistence 미완 → correction도 실행 안 됨
    expect(onCorrection).not.toHaveBeenCalled();
    resolveFinal();
    await new Promise((r) => setTimeout(r, 0));
    expect(order).toEqual(['final', 'correction']); // 순서 보장
  });

  it('stopStreaming waits for in-flight final/correction persistence before resolving', async () => {
    let resolveFinal: () => void = () => {};
    let finalDone = false;
    const onFinal = vi.fn().mockImplementation(() =>
      new Promise<void>((res) => { resolveFinal = () => { finalDone = true; res(); }; })
    );
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    const stopP = bridge.stopStreaming();
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    // stopped ack가 왔어도 final persistence가 안 끝났으면 stopStreaming은 resolve되면 안 됨
    let stopResolved = false;
    void stopP.then(() => { stopResolved = true; });
    await new Promise((r) => setTimeout(r, 0));
    expect(stopResolved).toBe(false);
    expect(finalDone).toBe(false);
    resolveFinal();
    await stopP;
    expect(finalDone).toBe(true);
  });

  it('drops audio chunks sent after stopStreaming begins (pause/stop gate)', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    bridge.addAudioChunk(Buffer.from([1, 2, 3]));
    const sentBefore = ws.sent.filter((s: string) => JSON.parse(s).type === 'audio').length;
    expect(sentBefore).toBe(1);
    // stop 시작 → 즉시 accepting=false. 이후 청크는 전송되지 않아야 함.
    const stopP = bridge.stopStreaming();
    bridge.addAudioChunk(Buffer.from([4, 5, 6]));
    const sentAfter = ws.sent.filter((s: string) => JSON.parse(s).type === 'audio').length;
    expect(sentAfter).toBe(1); // 증가 없음
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    await stopP;
  });

  it('keeps the latest correction when duplicates arrive before final (no timer leak)', async () => {
    vi.useFakeTimers();
    const order: string[] = [];
    const onCorrection = vi.fn().mockImplementation((_rid: string, _o: string, corrected: string) => { order.push(corrected); });
    const onFinal = vi.fn().mockResolvedValue(undefined);
    const bridge = new PipecatBridgeService({
      url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true, onCorrection,
    });
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    // final보다 먼저 같은 resultId의 correction 2개 도착
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'correction', meetingId: 'm1', resultId: 'r1', original: 'x', corrected: 'V1' }));
    vi.advanceTimersByTime(1000);
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'correction', meetingId: 'm1', resultId: 'r1', original: 'x', corrected: 'V2' }));
    // 첫 correction의 원래 타임아웃(5s) 시점을 지나도, 갱신된 타이머라 폐기되면 안 됨
    vi.advanceTimersByTime(4500); // 누적 5500ms (첫 엔트리 기준 초과) — 하지만 타이머는 두 번째 set 기준
    // 이제 final 도착 → 최신(V2) correction이 살아 실행되어야 함
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    await vi.runAllTimersAsync();
    expect(order).toEqual(['V2']);
    vi.useRealTimers();
  });

  it('surfaces onError and stops accepting audio on unexpected close (not during stop)', async () => {
    const onError = vi.fn();
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), onError);
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    // 서버가 갑자기 끊김(stop 호출 안 한 상태)
    ws.emit('close');
    expect(onError).toHaveBeenCalledOnce();
    // 이후 audio는 더 이상 전송되지 않아야 함(blackhole 방지)
    const audioBefore = ws.sent.filter((s: string) => JSON.parse(s).type === 'audio').length;
    bridge.addAudioChunk(Buffer.from([1, 2, 3]));
    const audioAfter = ws.sent.filter((s: string) => JSON.parse(s).type === 'audio').length;
    expect(audioAfter).toBe(audioBefore);
  });

  it('stopStreaming is idempotent: concurrent calls share one drain and send stop once', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    const stopP1 = bridge.stopStreaming();
    const stopP2 = bridge.stopStreaming();
    expect(stopP1).toBe(stopP2); // 같은 promise 공유
    const stopMsgs = ws.sent.filter((s: string) => JSON.parse(s).type === 'stop').length;
    expect(stopMsgs).toBe(1); // stop은 한 번만 전송
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    await Promise.all([stopP1, stopP2]);
  });

  it('stopStreaming rejects (degraded) when socket closes before stopped ack', async () => {
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), vi.fn(), vi.fn());
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    const stopP = bridge.stopStreaming();
    // stopped ack 없이 서버가 끊김(close during stop)
    ws.emit('close');
    await expect(stopP).rejects.toThrow(/stopped|유실|drain/);
  });

  it('stopStreaming rejects (degraded) when a final persistence fails', async () => {
    const onFinal = vi.fn().mockRejectedValue(new Error('db write failed'));
    const onError = vi.fn();
    const bridge = new PipecatBridgeService({ url: 'ws://localhost:8765', language: 'ko-KR', enableCorrection: true });
    const p = bridge.startStreaming('m1', vi.fn(), onFinal, onError);
    const ws = lastSocket();
    ws.emit('open');
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'ready', meetingId: 'm1' }));
    await p;
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'final', meetingId: 'm1', resultId: 'r1', text: 'x', startTime: 0, endTime: 1 }));
    const stopP = bridge.stopStreaming();
    ws.emit('message', JSON.stringify({ v: PROTOCOL_VERSION, type: 'stopped', meetingId: 'm1' }));
    await expect(stopP).rejects.toThrow(/저장|실패|persist/i);
    expect(onError).toHaveBeenCalled(); // 실패가 표면화됨
  });
});
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `npm test -- pipecat-bridge`
Expected: FAIL — 모듈 없음.

- [ ] **Step 4: PipecatBridgeService 구현**

`src/main/services/pipecat-bridge.service.ts`:

```ts
/**
 * Pipecat Bridge Service
 *
 * 로컬 Pipecat 서버(WebSocket)에 연결해 STT+LLM 파이프라인을 구동하는 StreamingBackend.
 * AWS SDK 직접 호출 대신, 오디오를 서버로 보내고 전사/교정 결과를 받아 콜백으로 전달한다.
 */
import WebSocket from 'ws';
import { v4 as uuidv4 } from 'uuid';
import type { StreamingBackend, StreamingBackendKind } from './streaming-backend';
import type { TranscriptionSegment } from '../../shared/types/transcription';
import {
  PROTOCOL_VERSION,
  ServerMessageSchema,
  type ServerMessage,
} from '../../shared/types/pipecat-protocol';
import { createLogger } from './logger.service';

const log = createLogger('pipecat-bridge');

const READY_TIMEOUT_MS = 10000;
const STOP_DRAIN_TIMEOUT_MS = 3000;

const ORPHAN_CORRECTION_TIMEOUT_MS = 5000;

export interface PipecatBridgeConfig {
  url: string; // ws://localhost:8765
  language: string;
  targetLanguage?: string;
  vocabularyName?: string;
  enableCorrection: boolean;
  // correction을 DB에 반영하는 핸들러(Bridge는 DB를 모름). resultId로 main이 segment row를 찾아 저장.
  onCorrection?: (resultId: string, original: string, corrected: string) => void | Promise<void>;
}

export class PipecatBridgeService implements StreamingBackend {
  readonly kind: StreamingBackendKind = 'pipecat';

  private ws: WebSocket | null = null;
  private config: PipecatBridgeConfig;
  private meetingId = '';
  private seq = 0;
  private accepting = false; // 로컬 audio 수용 게이트(pause/stop 즉시 차단용)
  private stopping = false; // stop 진행 중 플래그(idempotency + unexpected-close 구분용)
  private stopPromise: Promise<void> | null = null; // 진행 중 stop을 공유(멱등)
  private stopAcked = false; // 서버 'stopped' ack를 실제로 받았는지(ack 없는 close와 구분)
  private persistenceFailed = false; // 영속 작업(onFinal/onCorrection) 실패 여부
  private seenResultIds = new Set<string>();
  private onPartial: (text: string, speaker: string | null) => void = () => {};
  private onFinal: (s: TranscriptionSegment) => void | Promise<void> = () => {};
  private onError: (e: Error) => void = () => {};
  private stoppedResolve: (() => void) | null = null;

  // in-flight 영속 작업 추적(stop drain barrier용)
  private inflight = new Set<Promise<void>>();
  // resultId별 final 처리 promise(correction 순서 보장용)
  private finalPromiseByResult = new Map<string, Promise<void>>();
  // final보다 먼저 도착한 correction 보류 버퍼
  private pendingCorrections = new Map<string, { original: string; corrected: string; timer: ReturnType<typeof setTimeout> }>();

  constructor(config: PipecatBridgeConfig) {
    this.config = config;
  }

  private track(p: Promise<void>): Promise<void> {
    // 영속 작업 실패는 삼키지 않는다. 로그 + onError로 표면화하고 실패 플래그를 세워
    // stopStreaming이 깨끗한 완료로 보고하지 못하게 한다(조용한 tail 유실 방지).
    const wrapped = Promise.resolve(p).catch((err) => {
      this.persistenceFailed = true;
      log.error({ err: String(err) }, 'in-flight persistence task failed');
      this.onError(new Error(`전사/교정 저장에 실패했습니다: ${String(err)}`));
    });
    this.inflight.add(wrapped);
    void wrapped.finally(() => this.inflight.delete(wrapped));
    return wrapped;
  }

  startStreaming(
    meetingId: string,
    onPartialResult: (text: string, speakerLabel: string | null) => void,
    onFinalResult: (segment: TranscriptionSegment) => void | Promise<void>,
    onError: (error: Error) => void
  ): Promise<void> {
    this.meetingId = meetingId;
    this.onPartial = onPartialResult;
    this.onFinal = onFinalResult;
    this.onError = onError;

    return new Promise<void>((resolve, reject) => {
      let settled = false;
      const ws = new WebSocket(this.config.url);
      this.ws = ws;

      const readyTimer = setTimeout(() => {
        if (!settled) {
          settled = true;
          reject(new Error('Pipecat 서버 ready 응답 시간 초과'));
        }
      }, READY_TIMEOUT_MS);

      ws.on('open', () => {
        this.send({
          v: PROTOCOL_VERSION,
          type: 'start',
          meetingId,
          language: this.config.language,
          targetLanguage: this.config.targetLanguage,
          vocabularyName: this.config.vocabularyName,
          enableCorrection: this.config.enableCorrection,
        });
      });

      ws.on('message', (raw: WebSocket.RawData) => {
        const msg = this.parse(raw);
        if (!msg) return;
        if (msg.type === 'ready' && !settled) {
          settled = true;
          clearTimeout(readyTimer);
          this.accepting = true; // ready 이후에만 audio 전송 허용
          resolve();
          return;
        }
        this.handleMessage(msg);
      });

      ws.on('error', (err: Error) => {
        const friendly = new Error(
          `Pipecat 서버에 연결할 수 없습니다. 'cd server && python bot.py'로 서버를 먼저 실행하세요. (${err.message})`
        );
        if (!settled) {
          settled = true;
          clearTimeout(readyTimer);
          reject(friendly);
        } else {
          this.onError(friendly);
        }
      });

      ws.on('close', () => {
        if (this.stopping) {
          // stop 진행 중 close: drain 대기 해제(정상/비정상 구분은 stopAcked로 runStop이 판단)
          if (this.stoppedResolve) {
            this.stoppedResolve();
            this.stoppedResolve = null;
          }
          return;
        }
        // 예기치 않은 close(서버 재시작/크래시 등): audio를 조용히 버리지 않도록 fail-fast.
        this.accepting = false;
        if (settled) {
          this.onError(new Error('Pipecat 서버 연결이 끊겼습니다. 서버 상태를 확인하고 회의를 다시 시작하세요.'));
        }
        // settled=false(=ready 전 close)는 위 'error'/timeout 경로가 reject를 처리한다.
      });
    });
  }

  addAudioChunk(chunk: Buffer): void {
    // 로컬 게이트: pause/stop 이후 도착하는 청크는 즉시 무시(drain 윈도우 동안 발화 유입 방지).
    if (!this.accepting) return;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.send({
        v: PROTOCOL_VERSION,
        type: 'audio',
        meetingId: this.meetingId,
        seq: this.seq++,
        data: chunk.toString('base64'),
      });
    }
  }

  stopStreaming(): Promise<void> {
    // 멱등: 이미 stop이 진행 중이면 같은 promise를 반환(동시 호출자가 drain 결과를 공유).
    if (this.stopPromise) return this.stopPromise;
    // 가장 먼저 로컬 audio 수용을 차단(AWS TranscribeService가 isStreaming=false로 즉시 막는 것과 동일 취지).
    this.accepting = false;
    this.stopping = true;
    this.stopPromise = this.runStop();
    return this.stopPromise;
  }

  private async runStop(): Promise<void> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.ws = null;
      // 소켓이 이미 닫혀 있으면 서버 drain을 보장할 수 없다 → degraded.
      throw new Error('Pipecat 연결이 이미 닫혀 정상 종료(drain)를 보장할 수 없습니다.');
    }
    this.send({ v: PROTOCOL_VERSION, type: 'stop', meetingId: this.meetingId });

    // 1단계: stopped ack(또는 socket close, 또는 타임아웃) 대기 — 서버가 tail 프레임을 다 보냄
    let timedOut = false;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        timedOut = true;
        this.stoppedResolve = null;
        resolve();
      }, STOP_DRAIN_TIMEOUT_MS);
      // 'stopped' ack 또는 'close' 이벤트가 오면 호출됨 (handleMessage / ws.on('close'))
      this.stoppedResolve = () => {
        clearTimeout(timer);
        resolve();
      };
    });

    // 2단계: 수신한 final/correction의 비동기 영속 작업이 모두 끝날 때까지 대기(tail 유실 방지)
    await Promise.allSettled(Array.from(this.inflight));

    try { this.ws?.close(); } catch { /* noop */ }
    this.ws = null;

    // 3단계: 종료 품질 판정. ack 없는 close/타임아웃 또는 영속 실패는 degraded로 보고(throw)
    // → 핸들러가 회의를 '깨끗한 완료'로 처리하지 않도록 한다.
    if (!this.stopAcked) {
      throw new Error('Pipecat 서버 종료 ack(stopped)를 받지 못해 일부 전사가 유실됐을 수 있습니다.');
    }
    if (timedOut) {
      throw new Error('Pipecat 종료 drain 시간 초과로 일부 전사가 유실됐을 수 있습니다.');
    }
    if (this.persistenceFailed) {
      throw new Error('일부 전사/교정 저장에 실패했습니다.');
    }
  }

  private handleMessage(msg: ServerMessage): void {
    switch (msg.type) {
      case 'partial':
        this.onPartial(msg.text, msg.speakerLabel ?? null);
        break;
      case 'final': {
        if (this.seenResultIds.has(msg.resultId)) return; // 중복 억제(보조)
        this.seenResultIds.add(msg.resultId);
        const segment: TranscriptionSegment = {
          id: uuidv4(),
          meetingId: msg.meetingId,
          resultId: msg.resultId,
          text: msg.text,
          startTime: msg.startTime,
          endTime: msg.endTime,
          speakerLabel: msg.speakerLabel ?? null,
          confidence: msg.confidence ?? undefined,
          createdAt: new Date(),
        };
        // final 영속 promise를 추적하고 resultId에 매핑(correction 순서 보장용)
        const finalP = this.track(Promise.resolve(this.onFinal(segment)));
        this.finalPromiseByResult.set(msg.resultId, finalP);
        // 이미 보류 중인 correction이 있으면 final 완료 후 처리
        const pending = this.pendingCorrections.get(msg.resultId);
        if (pending) {
          clearTimeout(pending.timer);
          this.pendingCorrections.delete(msg.resultId);
          this.runCorrectionAfterFinal(msg.resultId, pending.original, pending.corrected);
        }
        break;
      }
      case 'correction': {
        const finalP = this.finalPromiseByResult.get(msg.resultId);
        if (finalP) {
          // 매칭 final이 이미 도착 → 그 영속 완료 후 correction 실행(순서 보장)
          this.runCorrectionAfterFinal(msg.resultId, msg.original, msg.corrected);
        } else {
          // final보다 먼저 도착 → 보류 버퍼에 넣고 타임아웃 시 폐기(고아 방지).
          // 같은 resultId의 재시도/중복 correction이 오면 최신 payload로 갱신하되,
          // 반드시 기존 타이머를 먼저 clear한다(오래된 타이머가 새 엔트리를 지우는 race 방지).
          const original = msg.original;
          const corrected = msg.corrected;
          const resultId = msg.resultId;
          const existing = this.pendingCorrections.get(resultId);
          if (existing) clearTimeout(existing.timer);
          const timer = setTimeout(() => {
            this.pendingCorrections.delete(resultId);
            log.warn({ resultId }, 'Orphan correction timed out, dropping');
          }, ORPHAN_CORRECTION_TIMEOUT_MS);
          this.pendingCorrections.set(resultId, { original, corrected, timer });
        }
        break;
      }
      case 'stopped':
        this.stopAcked = true;
        if (this.stoppedResolve) { this.stoppedResolve(); this.stoppedResolve = null; }
        break;
      case 'error':
        this.onError(new Error(msg.message));
        break;
    }
  }

  private runCorrectionAfterFinal(resultId: string, original: string, corrected: string): void {
    if (!this.config.onCorrection) return;
    const finalP = this.finalPromiseByResult.get(resultId) ?? Promise.resolve();
    const cb = this.config.onCorrection;
    this.track(finalP.then(() => Promise.resolve(cb(resultId, original, corrected))));
  }

  private parse(raw: WebSocket.RawData): ServerMessage | null {
    try {
      const json = JSON.parse(raw.toString());
      const result = ServerMessageSchema.safeParse(json);
      if (!result.success) {
        log.warn({ err: result.error.message }, 'Invalid server message');
        return null;
      }
      return result.data;
    } catch {
      return null;
    }
  }

  private send(msg: unknown): void {
    this.ws?.send(JSON.stringify(msg));
  }
}
```

> 참고: Bridge는 DB를 모른다. `correction`을 DB에 반영하는 실제 로직은 Task 8의 `onCorrection` 콜백(핸들러가 주입)이 담당한다. Bridge가 보장하는 것: (a) final 영속 promise를 resultId에 매핑, (b) correction이 그 final 완료 뒤 실행되도록 순서 보장, (c) final보다 먼저 온 correction을 타임아웃까지 보류(중복 시 기존 타이머 clear), (d) `stopStreaming`에서 stopped ack + 모든 in-flight 영속 작업 대기, (e) `accepting` 게이트로 pause/stop 후 audio 차단, (f) **예기치 않은 close(stop 중 아님)는 fail-fast**(`accepting=false` + `onError`, audio blackhole 방지), (g) `stopStreaming` **멱등**(진행 중 stop promise 공유, `stop` 1회 전송), (h) **degraded 종료 보고**: stopped ack 미수신/타임아웃/영속 실패 시 `stopStreaming`이 reject → 핸들러가 "조용한 완료"로 위장하지 않음. (i) 영속 실패는 `track()`에서 삼키지 않고 `onError` + 실패 플래그로 표면화.

- [ ] **Step 5: 테스트 통과 확인**

Run: `npm test -- pipecat-bridge`
Expected: PASS (13개 테스트 — kind/ready/final-mapping/dup-drop/stop-ack/correction-ordering/stop-waits-inflight/pause-audio-gate/duplicate-correction/unexpected-close/stop-idempotent/stop-rejects-on-close-before-ack/stop-rejects-on-persistence-failure)

- [ ] **Step 6: Commit**

```bash
git add package.json package-lock.json src/main/services/pipecat-bridge.service.ts src/main/services/__tests__/pipecat-bridge.service.test.ts
git commit -m "feat(pipecat): add PipecatBridgeService WS client backend with drain/ack"
```

---

## Task 8: 핸들러 배선 — start 분기, AUDIO_CHUNK, pause/resume/stop, correction

**Files:**
- Modify: `src/main/ipc/meeting.handlers.ts`

> 이 태스크는 통합 배선이라 테스트는 기존 핸들러 회귀(빌드 + 기존 테스트)로 검증하고, Bridge/세션 단위 동작은 Task 5·7에서 이미 커버했다. (`onCorrection` 콜백은 Task 7에서 이미 `PipecatBridgeConfig`에 포함됨.)

- [ ] **Step 1: 핸들러에 PipecatBridge import 및 상수 추가**

`src/main/ipc/meeting.handlers.ts` 상단 import에 추가:

```ts
import { PipecatBridgeService } from '../services/pipecat-bridge.service';
import { v4 as uuidv4 } from 'uuid'; // 이미 import되어 있으면 생략
```

파일 상단(로거 아래)에 서버 URL 상수 추가:

```ts
const PIPECAT_SERVER_URL = process.env.PIPECAT_SERVER_URL ?? 'ws://localhost:8765';
```

- [ ] **Step 2: correction → DB 저장 헬퍼 함수 추가**

`createFinalResultHandler` 함수 아래에 추가:

```ts
async function handlePipecatCorrection(
  meetingId: string,
  resultId: string,
  original: string,
  corrected: string
): Promise<void> {
  const db = meetingCorrectionService.ensureDatabase();
  const segmentIds = db.getSegmentRowIdsByResultId(meetingId, resultId);
  if (segmentIds.length === 0) {
    log.warn({ meetingId, resultId }, 'Orphan correction (no matching segment), dropping');
    return;
  }
  const corrected_sentence = {
    id: uuidv4(),
    meetingId,
    originalText: original,
    correctedText: corrected,
    translatedText: null,
    segmentIds,
    startTime: 0,
    endTime: 0,
    speakerLabel: null,
    modelId: 'pipecat-bedrock',
  };
  db.saveCorrectedSentence(corrected_sentence);
  sendToRenderer(IPC_CHANNELS.TRANSCRIPTION_CORRECTED, {
    id: corrected_sentence.id,
    originalText: original,
    correctedText: corrected,
    translatedText: null,
    segmentIds,
    speakerLabel: null,
    startTime: 0,
    endTime: 0,
  });
}
```

- [ ] **Step 4: `startStreaming` 헬퍼에 agentic 분기 추가**

`startStreaming` 헬퍼(line 218-240)를 다음으로 교체:

```ts
  const startStreaming = async (
    meetingId: string,
    meetingType: MeetingType,
    settings: SettingsInput,
    credentials: CredentialsInput | null,
    languageOverride?: TranscribeLanguage
  ): Promise<void> => {
    if (meetingType === 'agentic') {
      const language = languageOverride ?? settings.transcribe.languageCode;
      const bridge = new PipecatBridgeService({
        url: PIPECAT_SERVER_URL,
        language,
        targetLanguage: settings.transcribe.translationTargetLanguage,
        vocabularyName: settings.transcribe.vocabularyName,
        enableCorrection: settings.bedrock.enableCorrection,
        onCorrection: (resultId, original, corrected) =>
          handlePipecatCorrection(meetingId, resultId, original, corrected),
      });
      sessionManager.updateSession({ backend: bridge, backendKind: 'pipecat' });
      await bridge.startStreaming(
        meetingId,
        handlePartialResult,
        createFinalResultHandler(),
        handleTranscriptionError
      );
      return;
    }

    // 기존 AWS 경로 — credentials 필수
    if (!credentials) {
      throw new Error('AWS credentials not configured');
    }
    const session = meetingStreamingService.startStreaming(
      {
        meetingId,
        meetingType,
        credentials,
        transcribeSettings: settings.transcribe,
        bedrockSettings: settings.bedrock,
        languageOverride,
      },
      {
        onPartialResult: handlePartialResult,
        onFinalResult: createFinalResultHandler(),
        onError: handleTranscriptionError,
      }
    );
    // AWS 경로: 세션의 backend를 transcribeService로 설정
    if (session.transcribeService) {
      sessionManager.updateSession({
        backend: session.transcribeService,
        backendKind: 'aws',
      });
    }
  };
```

> 주의: `meetingStreamingService.startStreaming`은 내부에서 `sessionManager.createSession`을 호출하므로, agentic 분기에서는 `createSession`이 이미 `MEETING_START` 핸들러(line 359)에서 실행된 상태다. agentic은 `meetingStreamingService`를 거치지 않으므로 backend만 `updateSession`으로 붙인다.

- [ ] **Step 5: `MEETING_START`의 credential·vocabulary gate를 agentic이 우회하도록 변경**

> **중요(Codex 지적 #1):** 현재 `MEETING_START`는 line 266-269에서 meeting type을 따지기 **전에** `getCredentials()`가 null이면 `'AWS credentials not configured'`로 즉시 실패한다. agentic은 앱 자격증명이 불필요(서버가 AWS 인증 소유)하므로, 이 gate를 type 확인 뒤로 옮긴다.

`MEETING_START` 핸들러(line 259~377)에서 다음과 같이 수정한다.

(1) `meeting`을 먼저 조회해 `meeting.type`을 안 뒤 credential gate를 적용하도록 순서를 조정한다. 현재 `getMeeting`은 credential 체크 뒤(line 272-276)에 있으므로, credential 체크 블록(266-269)을 `meeting` 조회 뒤로 이동하고 agentic 분기를 추가:

```ts
      const settings = await getSettings();
      const db = meetingCorrectionService.ensureDatabase();
      const meeting = db.getMeeting(meetingId);
      if (!meeting) {
        return { success: false, error: 'Meeting not found' };
      }

      // agentic은 앱 자격증명이 불필요(Pipecat 서버가 AWS 인증 소유). 그 외 모드만 gate 적용.
      const isAgentic = meeting.type === 'agentic';
      const credentials = isAgentic ? null : await getCredentials();
      if (!isAgentic && !credentials) {
        return { success: false, error: 'AWS credentials not configured' };
      }
```

> 위 블록은 기존 line 266-276 영역(credential 조회·체크 + getSettings + getMeeting)을 대체한다. 기존에 `const credentials = await getCredentials();`가 먼저 있고 `getSettings`/`getMeeting`이 뒤따르던 순서를 뒤집는 것이다.

(2) 용어집(vocabulary) 해석 블록(line 281-356)은 AWS Transcribe 전용이므로 agentic이면 통째로 건너뛴다. 해당 `try { ... } catch` 블록을 `if (!isAgentic) { ... }`로 감싼다.

(3) `startStreaming` 호출부(line 374)는 `credentials`가 null일 수 있으므로 타입을 맞춘다. agentic 분기(Step 4)는 `credentials`를 사용하지 않으므로, 호출 시 `credentials ?? ({} as CredentialsInput)`로 넘기거나, `startStreaming` 헬퍼의 `credentials` 파라미터를 `CredentialsInput | null`로 바꾼다. 후자를 택한다:

`startStreaming` 헬퍼 시그니처(Step 4에서 만든 것)의 `credentials: CredentialsInput`를 `credentials: CredentialsInput | null`로 바꾸고, AWS 분기 진입 시 `if (!credentials) throw new Error('AWS credentials not configured');`로 가드한다.

- [ ] **Step 5b: `MEETING_RESUME`의 credential·vocabulary gate도 동일하게 우회**

> 동일 문제가 `MEETING_RESUME`(line 418-420 credential gate, 425-460 vocabulary)에도 있다.

`MEETING_RESUME` 핸들러에서:

```ts
      const session = sessionManager.getSession();
      if (!session) {
        return { success: false, error: 'No active meeting' };
      }

      const isAgentic = session.meetingType === 'agentic';
      const credentials = isAgentic ? null : await getCredentials();
      if (!isAgentic && !credentials) {
        return { success: false, error: 'AWS credentials not configured' };
      }

      const settings = await getSettings();
```

그리고 vocabulary 해석 블록(line 425-460)을 `if (!isAgentic) { ... }`로 감싼다. `startStreaming` 호출(line 468)은 Step 5의 (3)에서 시그니처를 `CredentialsInput | null`로 바꿨으므로 `credentials`를 그대로 넘긴다.

- [ ] **Step 5c: resume 시 저장된 세션 target language로 스트리밍 config 재구성**

> **중요(Codex 지적 #3):** 현재 `MEETING_RESUME`은 전역 `settings`를 그대로 `startStreaming`에 넘긴다. 하지만 회의는 비기본 target language로 시작될 수 있고(`useMeeting.createAndStartMeeting`), 세션은 `session.targetLanguage`에 그 값을 보관한다. 전역 settings로 재구성하면 resume 후 번역/교정 대상 언어가 원래 회의와 달라진다. agentic·AWS 양쪽 모두 영향.

`MEETING_RESUME`의 `transcribeSettings` 구성(기존 line 463-466 `const transcribeSettings = { ...settings.transcribe, vocabularyName };`)을 세션 상태로 override하도록 수정한다:

```ts
      const transcribeSettings = {
        ...settings.transcribe,
        vocabularyName, // agentic이면 위 (vocabulary) 블록을 건너뛰므로 undefined
        // 저장된 세션 값으로 override (start 시점의 source/target 언어 유지)
        languageCode: session.language,
        translationTargetLanguage: session.targetLanguage,
      };
```

> `startStreaming(session.meetingId, session.meetingType, { ...settings, transcribe: transcribeSettings }, credentials, session.language)` 호출은 그대로 유지된다(이미 `session.language`를 languageOverride로 넘기고 있음). 위 변경으로 agentic 분기의 `targetLanguage: settings.transcribe.translationTargetLanguage`가 세션의 실제 target을 받게 된다.

- [ ] **Step 6: `AUDIO_CHUNK` 핸들러를 backend 경유로 변경**

`AUDIO_CHUNK` 핸들러(line 617-629):

```ts
  // AUDIO_CHUNK
  ipcMain.on(IPC_CHANNELS.AUDIO_CHUNK, (_event, params: unknown) => {
    const session = sessionManager.getSession();
    if (session?.backend) {
      const validated = AudioChunkSchema.safeParse(params);
      if (validated.success) {
        const buffer = base64ToBuffer(validated.data.data);
        session.backend.addAudioChunk(buffer);
      } else {
        log.error('Invalid audio chunk data');
      }
    }
  });
```

- [ ] **Step 7: `MEETING_PAUSE`를 backend 경유로 변경 (degraded 종료 허용)**

`MEETING_PAUSE`(line 392)의 `await meetingStreamingService.stopStreaming();`를 교체. `stopStreaming`은 ack 미수신/타임아웃/영속 실패 시 reject할 수 있으므로 try/catch로 감싸, **이미 영속된 데이터는 보존**하고 pause 자체는 진행하되 경고를 표면화한다(pause는 일시정지일 뿐이라 hard fail로 막지 않는다):

```ts
      if (session.backend) {
        try {
          await session.backend.stopStreaming();
        } catch (err) {
          log.warn({ err: String(err) }, 'Pause drain degraded (일부 tail 유실 가능)');
          handleTranscriptionError(err instanceof Error ? err : new Error(String(err)));
        }
      }
```

- [ ] **Step 8: `MEETING_STOP`을 backend 경유로 변경**

`MEETING_STOP`(line 484-486)의:

```ts
      if (session?.transcribeService) {
        await session.transcribeService.stopStreaming();
      }
```
를 아래로 교체. 핵심 의미 3가지를 명확히 반환한다:
- `success` — 핸들러가 정상 처리됐는가
- `streamStillActive` — **백엔드 스트림이 아직 살아있는가**(복구 가능 여부). backend stop을 한 번이라도 시도했으면 스트림은 더 이상 살아있지 않다(terminal).
- `degraded` — 종료는 됐지만 일부 tail 전사/교정이 유실됐을 수 있는가

```ts
      // backend stop을 시도하기 전에는 스트림이 살아있다(=복구 가능). 시도하면 terminal.
      let streamStillActive = true;
      let stopDegraded = false;

      if (session?.backend) {
        streamStillActive = false; // stop 시도 = 더 이상 복구 불가(terminal)
        try {
          await session.backend.stopStreaming();
        } catch (err) {
          // ack 미수신/타임아웃/영속 실패: backend는 이미 멈췄다. degraded로 표시(복구는 안 함).
          stopDegraded = true;
          log.warn({ err: String(err) }, 'Stop drain degraded (일부 tail 유실 가능)');
          handleTranscriptionError(err instanceof Error ? err : new Error(String(err)));
        }
      }

      // 이하 finalization(flush/correction/status/reset)은 backend가 이미 멈춘 뒤 수행된다.
      // 여기서 throw가 나도 스트림은 살아있지 않으므로 streamStillActive=false를 유지한 채
      // degraded로 보고한다(렌더러가 캡처를 되살리지 않도록). → catch 블록 참고.
```

기존 finalization 블록(flush/correction → updateMeetingStatus → resetSession)은 그대로 두되, 핸들러의 반환과 catch를 다음과 같이 바꾼다:

```ts
      return { success: true, streamStillActive, degraded: stopDegraded };
    } catch (error) {
      log.error({ err: error }, 'Failed to stop meeting');
      // backend stop을 이미 시도해 streamStillActive=false인 상태에서 finalization이 실패한 경우,
      // 스트림은 살아있지 않으므로 복구 가능한 실패로 위장하지 않는다(terminal + degraded).
      // (backend stop 이전 단계에서의 예외는 streamStillActive=true로 복구 가능.)
      // 위 try 스코프의 streamStillActive를 catch에서 참조할 수 있도록 선언 위치를 try 밖으로 올린다.
      return { success: false, error: String(error), streamStillActive, degraded: true };
    }
```

> **구현 주의:** `streamStillActive`/`stopDegraded` 선언을 `try` 블록 **밖**(핸들러 함수 최상단)으로 올려 catch에서도 참조 가능하게 한다. 기본값은 `streamStillActive = true`, `stopDegraded = false`. backend stop 시도 직전에 `streamStillActive = false`로 내린다.

> **중요(Codex 4·8차 지적):** stop이 ack 없는 close/타임아웃/영속 실패를 "깨끗한 완료"로 위장하지 않으며(degraded), backend stop을 이미 시도한 뒤의 어떤 실패도 **복구 가능(`completed:false`로 캡처 재개)으로 위장하지 않는다**(terminal). 렌더러는 `streamStillActive`가 true일 때만 캡처를 되살린다.

> resume은 `startStreaming(session.meetingId, session.meetingType, ...)`를 다시 호출하므로(line 468), 위 Step 4의 분기 덕에 agentic이면 자동으로 새 Bridge가 생성·연결된다. 추가 변경 불필요. (단, resume의 credential gate 우회는 Step 5b에서 처리.)

> 주의: `clearSession()`(session-manager)도 `backend.stopStreaming()`을 호출하므로 동일하게 reject할 수 있다. `clearSession`은 cleanup 경로이므로 내부에서 try/catch로 감싸 로그만 남기고 세션을 비운다(Task 5의 `clearSession` 구현에 `try { await this.session.backend.stopStreaming(); } catch (e) { /* log */ }` 가드를 추가한다).

- [ ] **Step 8b: degraded stop 결과를 렌더러 호출 체인 끝까지 전파 (자동 요약 차단)**

> **중요(Codex 5차 지적):** Step 8에서 `MEETING_STOP`이 `{ success, degraded }`를 반환하게 했지만, 현재 렌더러 체인은 `degraded`를 읽지 않는다. 그러면 ack 미수신/영속 실패로 truncated된 세션도 정상 완료로 처리되어 **잘린 transcript로 자동 요약이 생성**된다. degraded를 끝까지 전파해 자동 요약을 막아야 한다.

다음 5개 지점을 수정한다.

(1) **preload 타입** — `src/preload/preload.ts:164`의 `stopMeeting` 반환 타입에 `degraded`·`streamStillActive` 추가:

```ts
  stopMeeting: () => Promise<{ success: boolean; error?: string; degraded?: boolean; streamStillActive?: boolean }>;
```

(2) **`useMeeting.stopMeeting`** (`src/renderer/hooks/useMeeting.ts:138-153`) — 반환 시그니처를 `Promise<boolean>`에서 `Promise<{ completed: boolean; degraded: boolean; recoverable: boolean }>`로 바꾼다. 핵심: `success:false`라도 **스트림이 이미 죽었으면(`streamStillActive === false`) 복구하지 않고 completed로 처리**한다. 복구는 스트림이 살아있을 때만(`streamStillActive !== false`).

```ts
  const stopMeeting = useCallback(async (): Promise<{ completed: boolean; degraded: boolean; recoverable: boolean }> => {
    setRecordingState((prev) => ({ ...prev, status: 'processing' }));

    const result = await window.electronAPI.stopMeeting();
    // 스트림이 아직 살아있을 때만 복구 가능. (backend stop을 시도조차 안 한 경우)
    const recoverable = result.streamStillActive === true;

    if (!result.success) {
      setError(result.error || '녹음 종료 실패');
      if (recoverable) {
        // 스트림이 살아있다 → 녹음 상태로 복원해 이어가게 한다.
        setRecordingState((prev) => ({ ...prev, status: 'recording' }));
        return { completed: false, degraded: false, recoverable: true };
      }
      // 스트림이 이미 종료됨(terminal) → 복구하지 않고 완료 처리(잘렸을 수 있으므로 degraded).
      setRecordingState((prev) => ({ ...prev, status: 'completed' }));
      return { completed: true, degraded: true, recoverable: false };
    }

    setRecordingState((prev) => ({ ...prev, status: 'completed' }));
    if (result.degraded) {
      setError('일부 전사/교정이 저장되지 않았을 수 있습니다. 요약은 자동 생성되지 않습니다.');
    }
    return { completed: true, degraded: Boolean(result.degraded), recoverable: false };
  }, []);
```

> 주의: `stopMeeting`을 호출하는 다른 곳이 있으면(예: 기존 `boolean` 반환 가정) 함께 갱신한다. `rg -n "stopMeeting" src/renderer`로 확인.

(3) **`useAppState.handleEndRecording`** (`src/renderer/hooks/useAppState.ts:91-93`) — 결과를 그대로 반환:

```ts
  const handleEndRecording = useCallback(async () => {
    return await stopMeeting();
  }, [stopMeeting]);
```

(4) **`useRecordingControls.handleEnd`** (`src/renderer/hooks/useRecordingControls.ts:76-82`) — stop 결과를 받아 (i) hard fail(`completed: false`)이면 로컬 teardown을 하지 않아 캡처/상태 불일치(blackhole)를 막고, (ii) degraded면 `onRecordingComplete`(자동 요약)를 건너뛴다.

> **중요(Codex 7차 지적):** 기존 코드는 `stopCapture()`를 먼저 부르고 `onStatusChange('idle')`를 무조건 실행했다. 그런데 `useMeeting.stopMeeting`은 hard fail 시 `recordingState`를 `'recording'`으로 되돌린다. 그러면 캡처는 이미 멈췄는데 상태는 recording → 사용자가 말해도 오디오가 안 나가는 silent blackhole이 된다. 따라서 **teardown을 `completed` 기준으로 게이트**한다. stop IPC를 먼저 await하고, 성공했을 때만 캡처를 멈추고 idle로 전환한다:

```ts
  const handleEnd = useCallback(async () => {
    clearDurationTimer();
    const result = await onEndRecording(); // { completed, degraded, recoverable }
    if (!result?.completed && result?.recoverable) {
      // 복구 가능한 실패(스트림이 아직 살아있음): 캡처를 유지하고 녹음을 이어간다.
      // (캡처-상태 불일치 blackhole 방지) 타이머 재가동.
      startDurationTimer();
      return;
    }
    // completed === true 이거나, 복구 불가(terminal) 실패: 로컬 teardown 수행.
    stopCapture();
    onStatusChange?.('idle');
    if (result?.completed && !result.degraded) {
      onRecordingComplete?.(); // degraded가 아닐 때만 자동 요약
    }
  }, [clearDurationTimer, startDurationTimer, stopCapture, onEndRecording, onStatusChange, onRecordingComplete]);
```

> `startDurationTimer`는 이 훅이 이미 보유한 함수다(`handleStart`에서 사용). **복구 가능한** 실패에서만 타이머를 다시 켜 녹음을 이어가게 한다. terminal 실패(backend가 이미 멈춤)는 teardown 경로로 가 캡처-상태 불일치를 막는다.

> `useRecordingControls`의 props 타입에서 `onEndRecording: () => Promise<void>`를 `onEndRecording: () => Promise<{ completed: boolean; degraded: boolean; recoverable: boolean }>`로 바꾼다.

(5) **`MeetingView` prop 경계** — `onEndRecording`은 hook 직접 호출이 아니라 `MeetingView` → `useRecordingControls`로 prop을 타고 흐른다. 따라서 **`MeetingViewProps`의 타입도 반드시 함께 바꿔야 한다**(이걸 빠뜨리면 strict TS에서 빌드 실패 또는 payload가 `void`로 소실됨). `src/renderer/components/MeetingView.tsx:39`:

```ts
  onEndRecording: () => Promise<{ completed: boolean; degraded: boolean; recoverable: boolean }>;
```

`MeetingView`는 이 prop을 그대로 `useRecordingControls({ ..., onEndRecording, ... })`로 전달(line 222)하므로 추가 변경은 없다. `App.tsx:62`은 `onEndRecording={handleEndRecording}`로 주입하는데, (3)에서 `handleEndRecording`가 `{ completed, degraded }`를 반환하도록 바꿨으므로 타입이 일치한다.

(6) **경계 전수 확인 & 회귀 보장** — 구현 후 `rg -n "onEndRecording|stopMeeting" src/renderer src/preload`로 **모든** 경계(preload 타입, useMeeting, useAppState, MeetingViewProps, useRecordingControls, App 주입)가 새 반환 타입과 일치하는지 확인한다(`stopMeeting` grep만으로는 `onEndRecording` 경계를 놓친다). AWS 경로(`TranscribeService.stopStreaming`)는 reject하지 않으므로 `MEETING_STOP`이 항상 `degraded: false` → 기존 자동 요약 동작 그대로. degraded 차단은 Pipecat 경로 실패 시에만 발동. 마지막으로 `npm run build`로 strict 타입 전파가 전 경계에서 통과하는지 확인한다.

(7) **stop 경로 테스트** — `src/renderer/hooks/__tests__/useRecordingControls.test.ts`(없으면 신규)에서 `handleEnd`의 세 분기를 검증한다:
- **복구 가능 실패** `{ completed: false, degraded: false, recoverable: true }` (스트림 살아있음): `stopCapture` **미호출**, `onStatusChange('idle')` **미호출**, `onRecordingComplete` **미호출**, `startDurationTimer` **재호출**(녹음 지속).
- **terminal 실패** `{ completed: true, degraded: true, recoverable: false }` (backend 이미 멈춤): `stopCapture` **호출**, `onStatusChange('idle')` **호출**, `onRecordingComplete` **미호출**(degraded라 자동 요약 차단), `startDurationTimer` **미재호출**(캡처 되살리지 않음 — blackhole 방지).
- **정상 종료** `{ completed: true, degraded: false, recoverable: false }`: `stopCapture`/`idle`/`onRecordingComplete` 모두 정상 수행.

또한 핸들러 레벨 검증(통합 또는 mock)으로, `MEETING_STOP`이 `backend.stopStreaming()` 성공 **후** finalization(flush/correction/status)에서 throw해도 반환이 `{ success:false, streamStillActive:false, degraded:true }`임을 확인한다(= 렌더러가 캡처를 되살리지 않음).

- [ ] **Step 9: 빌드 + 기존 테스트 회귀 확인**

Run: `npm run build && npm test`
Expected: PASS — 컴파일 에러 없음, 기존 테스트 모두 통과.

- [ ] **Step 9b: agentic credential-free 시작 수동 확인**

앱 자격증명이 설정되지 않은 상태(또는 일부러 비운 상태)에서 Agentic Meeting을 시작했을 때 `'AWS credentials not configured'`로 막히지 **않고**, 대신 Pipecat 서버 연결을 시도하는지 확인한다. 서버 미실행이면 "서버 미연결" 안내(Task 10 배지)가 떠야 한다. (자동 핸들러 테스트가 어렵다면, 최소한 `npm start`로 자격증명 미설정 상태에서 agentic 진입→녹음 시작이 credential 에러 없이 진행되는지 확인.)

- [ ] **Step 9c: resume target language 보존 수동 확인**

비기본 target language(예: source `en-US`, target `ja-JP`)로 회의를 시작 → 일시정지 → 재개한 뒤, 재개된 세션이 전역 settings의 기본 target이 아니라 **원래 회의의 target(`ja-JP`)** 으로 동작하는지 확인한다. (AWS·agentic 양쪽 모두. agentic은 Bridge `start` 메시지의 `targetLanguage` 필드 값으로 확인 가능.)

- [ ] **Step 10: Commit**

```bash
git add src/main/ipc/meeting.handlers.ts src/main/services/pipecat-bridge.service.ts
git commit -m "feat(agentic): wire pipecat backend into start/audio/pause/stop and correction persistence"
```

---

## Task 9: Python Pipecat 서버 (server/)

**Files:**
- Create: `server/bot.py`, `server/requirements.txt`, `server/.env.example`, `server/README.md`, `server/.gitignore`

> Python 서버는 자동 테스트 범위 밖(수동 스모크). context7로 Pipecat WS transport/serializer 패턴을 재확인하며 작성한다.

- [ ] **Step 1: context7로 Pipecat 최신 패턴 재확인**

`mcp__context7__query-docs`(libraryId `/pipecat-ai/docs`)로 다음을 조회한다:
- "FastAPIWebsocketTransport custom serializer raw websocket text messages"
- "AWSTranscribeSTTService TranscriptionFrame fields and result id"
- "AWSBedrockLLMService streaming text frames in pipeline"

조회 결과에 맞춰 아래 `bot.py`의 serializer/프레임 처리 세부를 조정한다.

- [ ] **Step 2: `server/requirements.txt` 작성**

```
pipecat-ai[aws]
fastapi
uvicorn[standard]
python-dotenv
websockets
```

- [ ] **Step 3: `server/.env.example` 작성**

```
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_REGION=us-east-1
# Bedrock 모델 (Claude 등)
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
# 서버 바인딩 (로컬 데모 전용)
PIPECAT_HOST=localhost
PIPECAT_PORT=8765
```

- [ ] **Step 4: `server/.gitignore` 작성**

```
.env
.venv/
__pycache__/
*.pyc
```

- [ ] **Step 5: `server/bot.py` 작성**

> 아래는 골격이다. Step 1의 context7 조회 결과에 맞춰 serializer/프레임 매핑을 확정한다. 핵심 계약: 클라이언트가 보내는 `{type:"start"|"audio"|"stop"}` JSON을 받아 PCM을 STT에 흘리고, 전사/교정 결과를 `{type:"ready"|"partial"|"final"|"correction"|"stopped"|"error"}` JSON으로 돌려준다. final에는 반드시 `resultId`를 포함하고, `stop` 수신 시 drain 후 `stopped`를 보낸다.

```python
"""
Pipecat 기반 Meeting Assistant 사이드 서버.

Electron 앱이 WebSocket으로 PCM(16kHz mono Int16, base64) 오디오를 보내면
AWS Transcribe STT + Bedrock LLM 파이프라인을 거쳐 전사/교정 결과를 JSON으로 반환한다.

프로토콜은 src/shared/types/pipecat-protocol.ts 와 1:1로 맞춘다 (PROTOCOL_VERSION = 1).
"""
import asyncio
import base64
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

load_dotenv()

PROTOCOL_VERSION = 1
HOST = os.getenv("PIPECAT_HOST", "localhost")
PORT = int(os.getenv("PIPECAT_PORT", "8765"))

app = FastAPI()


@app.websocket("/")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    meeting_id = None
    result_index = 0

    async def send(obj):
        await websocket.send_text(json.dumps(obj))

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "start":
                meeting_id = msg["meetingId"]
                # TODO(context7 확인 후): AWSTranscribeSTTService / AWSBedrockLLMService로
                #   파이프라인을 구성하고, STT 결과 콜백에서 아래 형식으로 send 한다.
                #   transport.input() -> stt -> context_aggregator.user() -> llm -> output
                await send({"v": PROTOCOL_VERSION, "type": "ready", "meetingId": meeting_id})

            elif mtype == "audio":
                pcm = base64.b64decode(msg["data"])
                # TODO: pcm을 STT 파이프라인 입력 프레임으로 push.
                # STT가 partial/final 프레임을 내면 콜백에서:
                #   await send({"v":1,"type":"partial","meetingId":meeting_id,"text":...})
                #   result_index += 1
                #   await send({"v":1,"type":"final","meetingId":meeting_id,
                #               "resultId": f"{meeting_id}:{result_index}",
                #               "text":..., "startTime":..., "endTime":...})
                # LLM 교정 결과가 나오면:
                #   await send({"v":1,"type":"correction","meetingId":meeting_id,
                #               "resultId": <대응 final resultId>, "original":..., "corrected":...})
                pass

            elif mtype == "stop":
                # drain: 파이프라인에 남은 작업을 모두 flush하고 마지막 final/correction을 전송한 뒤
                # TODO: await pipeline.flush() 류 호출
                await send({"v": PROTOCOL_VERSION, "type": "stopped", "meetingId": meeting_id})
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        try:
            await send({"v": PROTOCOL_VERSION, "type": "error", "meetingId": meeting_id, "message": str(e)})
        except Exception:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
```

- [ ] **Step 6: `server/README.md` 작성**

```markdown
# Pipecat Side Server (Agentic Meeting)

Meeting Assistant의 `agentic` 모드가 사용하는 로컬 Pipecat 서버입니다.
Electron 앱과 별개로 **사용자가 직접 실행**합니다.

## 셋업

```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # AWS 자격증명 / 모델 ID 입력
```

## 실행

```bash
python bot.py
```

기본 `ws://localhost:8765`로 대기합니다. 앱에서 Agentic Meeting을 시작하면 연결됩니다.

## 보안 주의 (로컬 데모 전용)

- `.env`는 커밋하지 마세요(`.gitignore`에 포함). 장기 키 대신 단기 STS 토큰/프로파일 권장.
- 서버는 `localhost`에만 바인딩하며 외부에 노출하지 마세요.
```

- [ ] **Step 7: 수동 스모크 (선택, 환경 있을 때)**

Run:
```bash
cd server && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python bot.py
```
Expected: 서버가 `localhost:8765`에서 기동. (실제 STT/LLM 동작은 Step 1의 context7 결과로 채운 뒤 앱과 함께 검증.)

- [ ] **Step 8: Commit**

```bash
git add server/
git commit -m "feat(server): add pipecat side server scaffold (bot.py, requirements, README)"
```

---

## Task 10: Renderer — AgenticMeetingView + 라우팅

**Files:**
- Create: `src/renderer/components/meeting-types/AgenticMeetingView.tsx`
- Modify: `src/renderer/components/meeting-types/index.ts`
- Modify: `src/renderer/components/MeetingView.tsx:298-312`

- [ ] **Step 1: `AgenticMeetingView` 작성**

`TranslatedMeetingView`와 동일하게 `MeetingWorkspace`를 감싸되, "Pipecat 파이프라인 활성/서버 미연결" 상태 배지를 우측 패널에 노출한다. `src/renderer/components/meeting-types/AgenticMeetingView.tsx`:

```tsx
import { useEffect, useState } from 'react';
import type { MeetingWorkspaceProps } from '../meeting/MeetingWorkspace';
import MeetingWorkspace from '../meeting/MeetingWorkspace';

function AgenticMeetingView(props: MeetingWorkspaceProps) {
  const isRecording = props.recordingState.status === 'recording';
  const [serverError, setServerError] = useState<string | null>(null);

  // 전사 에러(서버 미연결 등)를 감지해 배지 상태 갱신
  useEffect(() => {
    if (!window.electronAPI?.onTranscriptionError) return;
    const off = window.electronAPI.onTranscriptionError((data) => {
      setServerError(data.error);
    });
    return off;
  }, []);

  const statusPanel = (
    <div className="qm-right-panel-card">
      <div className="qm-right-panel-card-header">
        <h4>Pipecat Pipeline</h4>
      </div>
      <div className="qm-ai-panel-body">
        {serverError ? (
          <div className="qm-empty-state qm-right-panel-empty">
            서버 미연결: {serverError}
            <br />
            <code>cd server &amp;&amp; python bot.py</code> 로 서버를 먼저 실행하세요.
          </div>
        ) : (
          <div className="qm-empty-state qm-right-panel-empty">
            {isRecording
              ? '🟢 Pipecat 파이프라인 활성 (STT + Bedrock via local server)'
              : '대기 중 — 녹음을 시작하면 로컬 Pipecat 서버에 연결합니다.'}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <MeetingWorkspace
      {...props}
      rightPanelContent={<div className="qm-right-panel-stack">{statusPanel}</div>}
    />
  );
}

export default AgenticMeetingView;
```

> 주의: `MeetingWorkspaceProps`의 실제 필드(`recordingState.status` 등)는 `meeting-types/types.ts`와 `MeetingWorkspace.tsx`를 열어 확인하고, `onTranscriptionError`의 시그니처는 preload(`src/preload/preload.ts:85`)와 일치시킨다. 위 코드의 prop 접근이 타입과 다르면 맞춘다.

- [ ] **Step 2: `index.ts`에 export 추가**

`src/renderer/components/meeting-types/index.ts`:

```ts
export { default as QuickMeetingView } from './QuickMeetingView';
export { default as ClientMeetingView } from './ClientMeetingView';
export { default as InterviewMeetingView } from './InterviewMeetingView';
export { default as TranslatedMeetingView } from './TranslatedMeetingView';
export { default as AgenticMeetingView } from './AgenticMeetingView';
/** @deprecated Use TranslatedMeetingView instead */
export { default as EnglishMeetingView } from './TranslatedMeetingView';
```

- [ ] **Step 3: `MeetingView.tsx`에 라우팅 추가**

`src/renderer/components/MeetingView.tsx`의 import(line 20-24)에 `AgenticMeetingView` 추가:

```tsx
import {
  // ...기존...
  QuickMeetingView,
  ClientMeetingView,
  InterviewMeetingView,
  TranslatedMeetingView,
  AgenticMeetingView,
} from './meeting-types';
```

switch 문(line 298-312)에 case 추가(`default` 위):

```tsx
    case 'agentic':
      return <AgenticMeetingView {...meetingWorkspaceProps} />;
    default:
      return <div>지원하지 않는 미팅 타입입니다.</div>;
```

- [ ] **Step 4: 빌드 + 타입체크**

Run: `npm run build`
Expected: PASS

- [ ] **Step 5: 앱 실행 스모크 (서버 미실행 상태)**

Run: `npm start`
Expected: 홈에 "Agentic Meeting" 카드 표시. 카드 진입 후 녹음 시작 시, 서버가 안 떠 있으면 "서버 미연결" 배지 + 안내 메시지가 보인다. (서버를 띄우면 전사가 표시된다 — Task 9 완성 후 종합 검증.)

- [ ] **Step 6: Commit**

```bash
git add src/renderer/components/meeting-types/AgenticMeetingView.tsx src/renderer/components/meeting-types/index.ts src/renderer/components/MeetingView.tsx
git commit -m "feat(agentic): add AgenticMeetingView and route 'agentic' meeting type"
```

---

## Task 11: 종합 검증 & 정리

**Files:** (없음 — 검증 전용)

- [ ] **Step 1: 전체 테스트 통과 확인**

Run: `npm test`
Expected: 모든 테스트 PASS (database, session-manager, pipecat-bridge, pipecat-protocol 포함).

- [ ] **Step 2: 빌드 확인**

Run: `npm run build`
Expected: PASS, 타입 에러 없음.

- [ ] **Step 3: 기존 모드 회귀 수동 확인**

`npm start`로 앱을 띄우고 기존 모드(Client/Quick/Translated/Interview) 중 하나로 녹음→일시정지→재개→정지를 수행. AWS 경로가 backend 추상화 도입 후에도 정상 동작(전사/교정/요약)하는지 확인.

- [ ] **Step 4: agentic 종단 검증 (서버 실행)**

`server/`에서 `.env` 설정 후 `python bot.py` 실행. 앱에서 Agentic Meeting 시작 → 말하기 → 실시간 전사 표시 → 정지 시 마지막 final까지 저장되는지 확인.

- [ ] **Step 5: 최종 커밋 (필요 시)**

```bash
git add -A
git commit -m "chore(agentic): final verification pass"
```
